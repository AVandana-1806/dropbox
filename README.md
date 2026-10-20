"""Pipeline Alert Notification
==============================

Formats CloudWatch alarm notifications for the CloudWatch-to-Splunk log
pipeline into an HTML email and sends it via SES. Subscribed to the alarm
SNS topic; only ALARM transitions are sent, recoveries are ignored.

Environment variables:
    FROM_EMAIL             SES-verified sender (required)
    TO_EMAILS              comma-separated recipients (required)
    ENVIRONMENT            label shown in the header, e.g. dev01 (required)
    DELIVERY_STREAM_NAME   Firehose stream to report on (required)
    TRANSFORM_FUNCTION     transform Lambda name (required)
    BACKUP_BUCKET          S3 bucket holding the failure prefixes (required)
    TEMPLATE_PATH          default: alert_email.html
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logger.setLevel(logging.INFO)
    for name in ("boto3", "botocore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


_configure_logging()

ROW = (
    '<tr{shade}>'
    '<td style="padding:8px 10px;border:1px solid #d8d8d8;">{stage}</td>'
    '<td style="padding:8px 10px;border:1px solid #d8d8d8;">{signal}</td>'
    '<td style="padding:8px 10px;border:1px solid #d8d8d8;{highlight}">{value}</td>'
    "</tr>"
)
SHADE = ' style="background-color:#fafafa;"'
ALERT_CELL = "background-color:#fbe9e9;color:#791f1f;font-weight:bold;"


@dataclass
class Config:
    """Runtime configuration read from environment variables."""

    from_email: str
    to_emails: list[str]
    environment: str
    delivery_stream: str
    transform_function: str
    backup_bucket: str
    template_path: Path
    region: str = field(default_factory=lambda: os.environ["AWS_REGION"])

    @classmethod
    def from_env(cls) -> "Config":
        """Build config from the Lambda environment."""
        return cls(
            from_email=os.environ["FROM_EMAIL"],
            to_emails=[
                address.strip()
                for address in os.environ["TO_EMAILS"].split(",")
                if address.strip()
            ],
            environment=os.environ["ENVIRONMENT"],
            delivery_stream=os.environ["DELIVERY_STREAM_NAME"],
            transform_function=os.environ["TRANSFORM_FUNCTION"],
            backup_bucket=os.environ["BACKUP_BUCKET"],
            template_path=Path(os.environ.get("TEMPLATE_PATH", "alert_email.html")),
        )


class PipelineState:
    """Collect current pipeline signals to accompany an alarm."""

    def __init__(self, config: Config, cloudwatch_client) -> None:
        self._config = config
        self._cloudwatch = cloudwatch_client

    def rows(self) -> str:
        """Render the pipeline-state table rows."""
        values = self._fetch()
        entries = [
            ("CloudWatch to Firehose", "IncomingRecords (15m)",
             f"{values['incoming']:,.0f}", values["incoming"] == 0),
            ("Transform", "Errors (15m)",
             f"{values['errors']:,.0f}", values["errors"] > 0),
            ("Transform", "Throttles (15m)",
             f"{values['throttles']:,.0f}", values["throttles"] > 0),
            ("Firehose to Splunk", "Delivery success",
             f"{values['success'] * 100:.2f}%", values["success"] < 0.999),
            ("Recovery", "Records rejected (15m)",
             f"{values['rejected']:,.0f}", values["rejected"] > 0),
        ]
        return "\n".join(
            ROW.format(
                shade=SHADE if index % 2 else "",
                stage=stage,
                signal=signal,
                value=value,
                highlight=ALERT_CELL if alert else "",
            )
            for index, (stage, signal, value, alert) in enumerate(entries)
        )

    def _fetch(self) -> dict:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=15)
        stream = ("DeliveryStreamName", self._config.delivery_stream)
        function = ("FunctionName", self._config.transform_function)
        queries = [
            self._query("incoming", "AWS/Firehose", "IncomingRecords", stream, "Sum"),
            self._query("errors", "AWS/Lambda", "Errors", function, "Sum"),
            self._query("throttles", "AWS/Lambda", "Throttles", function, "Sum"),
            self._query("success", "AWS/Firehose", "DeliveryToSplunk.Success",
                        stream, "Average"),
            self._query("rejected", "LogPipeline", "ProcessingFailed", None, "Sum"),
        ]
        response = self._cloudwatch.get_metric_data(
            MetricDataQueries=queries, StartTime=start, EndTime=end
        )
        values = {
            item["Id"]: sum(item["Values"]) if item["Values"] else 0.0
            for item in response["MetricDataResults"]
        }
        values["success"] = values.get("success", 1.0) or 1.0
        return values

    @staticmethod
    def _query(query_id: str, namespace: str, metric: str,
               dimension: tuple[str, str] | None, stat: str) -> dict:
        metric_spec: dict = {"Namespace": namespace, "MetricName": metric}
        if dimension:
            metric_spec["Dimensions"] = [
                {"Name": dimension[0], "Value": dimension[1]}
            ]
        return {
            "Id": query_id,
            "MetricStat": {"Metric": metric_spec, "Period": 900, "Stat": stat},
        }


class AlertEmail:
    """Render and send the alarm email."""

    def __init__(self, config: Config, ses_client) -> None:
        self._config = config
        self._ses = ses_client
        self._template = config.template_path.read_text(encoding="utf-8")

    def send(self, alarm: dict, state_rows: str) -> str:
        """Send the formatted alert; return the SES message id."""
        name = alarm["AlarmName"]
        trigger = alarm.get("Trigger", {})
        body = self._template.format(
            alarm_name=name,
            environment=self._config.environment,
            region=self._config.region,
            timestamp=alarm.get("StateChangeTime", ""),
            summary=alarm.get("AlarmDescription") or "No description configured.",
            metric_name=trigger.get("MetricName", "n/a"),
            namespace=trigger.get("Namespace", "n/a"),
            condition=self._condition(trigger),
            reason=alarm.get("NewStateReason", ""),
            state_rows=state_rows,
            alarm_url=self._alarm_url(name),
            logs_url=self._logs_url(),
        )
        response = self._ses.send_email(
            Source=self._config.from_email,
            Destination={"ToAddresses": self._config.to_emails},
            Message={
                "Subject": {"Data": f"[{self._config.environment}] Pipeline alert: {name}"},
                "Body": {"Html": {"Data": body}},
            },
        )
        return response["MessageId"]

    @staticmethod
    def _condition(trigger: dict) -> str:
        if not trigger:
            return "n/a"
        return (
            f"{trigger.get('Statistic', '')} {trigger.get('ComparisonOperator', '')} "
            f"{trigger.get('Threshold', '')} for {trigger.get('EvaluationPeriods', '')} "
            f"period(s) of {trigger.get('Period', '')}s"
        )

    def _alarm_url(self, name: str) -> str:
        region = self._config.region
        return (
            f"https://{region}.console.aws.amazon.com/cloudwatch/home"
            f"?region={region}#alarmsV2:alarm/{name}"
        )

    def _logs_url(self) -> str:
        region = self._config.region
        group = f"/aws/lambda/{self._config.transform_function}".replace("/", "$252F")
        return (
            f"https://{region}.console.aws.amazon.com/cloudwatch/home"
            f"?region={region}#logsV2:log-groups/log-group/{group}"
        )


CONFIG = Config.from_env()
SESSION = boto3.Session()
CLOUDWATCH_CLIENT = SESSION.client("cloudwatch")
SES_CLIENT = SESSION.client("ses")


def handler(event: dict, context: object) -> dict:
    """SNS entry point: send an email for each ALARM transition."""
    sent = 0
    for record in event.get("Records", []):
        try:
            alarm = json.loads(record["Sns"]["Message"])
        except (KeyError, ValueError) as exc:
            logger.error("unparseable SNS record: %s", exc)
            continue

        if alarm.get("NewStateValue") != "ALARM":
            logger.info("ignoring %s transition for %s",
                        alarm.get("NewStateValue"), alarm.get("AlarmName"))
            continue

        state_rows = PipelineState(CONFIG, CLOUDWATCH_CLIENT).rows()
        try:
            message_id = AlertEmail(CONFIG, SES_CLIENT).send(alarm, state_rows)
        except (ClientError, KeyError) as exc:
            logger.exception("could not send alert for %s: %s",
                             alarm.get("AlarmName"), exc)
            continue
        sent += 1
        logger.info("alert sent for %s (%s)", alarm["AlarmName"], message_id)

    return {"sent": sent}
