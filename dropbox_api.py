"""
AWS Lambda function to automate ACM DNS validation when the Route 53 hosted zone
lives in a different AWS account.
It reads the validation CNAMEs from the pending certificate, assumes the cross-account
Route 53 role in the DNS account and UPSERTs the records so the certificate is issued.
"""

import json
import os
import time
import logging
import boto3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("boto3").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)

CLOUDTRAIL_DETAIL_TYPE = "AWS API Call via CloudTrail"
RENEWAL_DETAIL_TYPE = "ACM Certificate Renewal Action Required"
SCHEDULED_DETAIL_TYPE = "Scheduled Event"
PENDING_STATUSES = ("PENDING_VALIDATION", "PENDING_AUTO_RENEWAL")


class AcmDnsValidationHandler:
    """
    Creates ACM DNS validation records in a Route 53 hosted zone owned by another account.
    """

    def __init__(self):
        """Initialize the AcmDnsValidationHandler class."""
        self.acm = boto3.client("acm")
        self.sts = boto3.client("sts")
        self.hosted_zone_id = os.environ["HOSTED_ZONE_ID"]
        self.hosted_zone_name = os.environ["HOSTED_ZONE_NAME"].rstrip(".").lower()
        self.dns_role_arn = os.environ["DNS_ROLE_ARN"]
        self.record_ttl = int(os.environ.get("RECORD_TTL", "300"))
        self.max_attempts = int(os.environ.get("DESCRIBE_MAX_ATTEMPTS", "12"))
        self.retry_delay = int(os.environ.get("DESCRIBE_RETRY_DELAY", "5"))

    def handle(self, event):
        """
        Resolves the certificate ARNs from the incoming event and validates each one.
        """
        certificate_arns = self.get_certificate_arns(event)
        logger.info("Certificates to process: %s", certificate_arns)

        results, failures = [], []
        for certificate_arn in certificate_arns:
            try:
                results.append(self.validate_certificate(certificate_arn))
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Failed to validate %s", certificate_arn)
                failures.append(certificate_arn)

        # Fail the invocation so Lambda async retry / OnFailure destination kicks in.
        if failures:
            raise RuntimeError(f"Failed to validate certificates: {failures}")
        return self._build_response(results)

    def get_certificate_arns(self, event):
        """
        Extract certificate ARNs from a CloudTrail RequestCertificate event, an ACM
        renewal event, a scheduled sweep, or a manual invoke ({"certificateArn": "..."}).
        """
        detail_type = event.get("detail-type")
        detail = event.get("detail", {})

        if detail_type == CLOUDTRAIL_DETAIL_TYPE:
            arn = (detail.get("responseElements") or {}).get("certificateArn")
            return [arn] if arn else []

        if detail_type == RENEWAL_DETAIL_TYPE:
            return event.get("resources", [])

        if detail_type == SCHEDULED_DETAIL_TYPE:
            return self.list_pending_certificates()

        if event.get("certificateArn"):
            return [event["certificateArn"]]

        logger.warning("Unsupported event: %s", json.dumps(event))
        return []

    def list_pending_certificates(self):
        """
        Lists certificates waiting on validation. Used by the scheduled sweep to catch
        any certificate whose event was missed (EventBridge delivery is best effort).
        """
        arns = []
        paginator = self.acm.get_paginator("list_certificates")
        for page in paginator.paginate(CertificateStatuses=["PENDING_VALIDATION"]):
            arns.extend(cert["CertificateArn"] for cert in page["CertificateSummaryList"])
        return arns

    def validate_certificate(self, certificate_arn):
        """
        Builds the validation records for a certificate and UPSERTs them in Route 53.
        """
        records = self.get_validation_records(certificate_arn)
        if not records:
            return {"certificateArn": certificate_arn, "action": "skipped"}

        change_id = self.upsert_records(certificate_arn, records)
        return {
            "certificateArn": certificate_arn,
            "action": "upserted",
            "records": [record["Name"] for record in records],
            "changeId": change_id,
        }

    def get_validation_records(self, certificate_arn):
        """
        Reads the DNS validation CNAMEs from ACM. ACM fills in ResourceRecord a few
        seconds after RequestCertificate, so this retries until every DNS domain has one.
        """
        for attempt in range(1, self.max_attempts + 1):
            certificate = self.acm.describe_certificate(CertificateArn=certificate_arn)[
                "Certificate"
            ]
            options = self._get_pending_validation_options(certificate)
            if options is None:
                logger.info(
                    "Certificate %s is %s, nothing to validate",
                    certificate_arn,
                    certificate.get("Status"),
                )
                return []

            dns_options = [o for o in options if o.get("ValidationMethod") == "DNS"]
            if dns_options and all("ResourceRecord" in o for o in dns_options):
                return self._build_records(certificate_arn, dns_options)

            logger.info(
                "Validation records not ready for %s (attempt %s/%s)",
                certificate_arn,
                attempt,
                self.max_attempts,
            )
            time.sleep(self.retry_delay)

        raise TimeoutError(f"ACM did not return validation records for {certificate_arn}")

    @staticmethod
    def _get_pending_validation_options(certificate):
        """
        Returns the domain validation options for a new certificate pending validation
        or for a managed renewal that is waiting on DNS. Returns None otherwise.
        """
        if certificate.get("Type") != "AMAZON_ISSUED":
            return None
        if certificate.get("Status") == "PENDING_VALIDATION":
            return certificate.get("DomainValidationOptions", [])

        renewal = certificate.get("RenewalSummary", {})
        if renewal.get("RenewalStatus") in PENDING_STATUSES:
            return renewal.get("DomainValidationOptions", [])
        return None

    def _build_records(self, certificate_arn, dns_options):
        """
        Converts ACM resource records into Route 53 record sets. Records outside the
        configured hosted zone are skipped, and duplicates (e.g. example.com and
        *.example.com share one CNAME) are collapsed.
        """
        records = {}
        zone_suffix = f".{self.hosted_zone_name}."
        for option in dns_options:
            resource_record = option["ResourceRecord"]
            name = resource_record["Name"].lower()
            if not name.endswith(zone_suffix):
                logger.warning(
                    "Skipping %s for %s, it is not in hosted zone %s",
                    name,
                    certificate_arn,
                    self.hosted_zone_name,
                )
                continue
            records[name] = {
                "Name": name,
                "Type": resource_record["Type"],
                "TTL": self.record_ttl,
                "ResourceRecords": [{"Value": resource_record["Value"]}],
            }
        return list(records.values())

    def upsert_records(self, certificate_arn, records):
        """
        Assumes the cross-account role and UPSERTs the records. UPSERT makes the call
        idempotent, so re-running for the same certificate is safe.
        """
        route53 = self._get_dns_account_client(certificate_arn)
        response = route53.change_resource_record_sets(
            HostedZoneId=self.hosted_zone_id,
            ChangeBatch={
                "Comment": f"ACM DNS validation for {certificate_arn}",
                "Changes": [
                    {"Action": "UPSERT", "ResourceRecordSet": record} for record in records
                ],
            },
        )
        change_id = response["ChangeInfo"]["Id"]
        # No INSYNC wait: ACM keeps polling DNS on its own, and Route 53 propagation can
        # take longer than a Lambda should block for.
        logger.info(
            "UPSERTed %s in %s (change %s)",
            [record["Name"] for record in records],
            self.hosted_zone_id,
            change_id,
        )
        return change_id

    def _get_dns_account_client(self, certificate_arn):
        """
        Returns a Route 53 client using credentials of the role in the DNS account.
        """
        certificate_id = certificate_arn.split("/")[-1]
        credentials = self.sts.assume_role(
            RoleArn=self.dns_role_arn,
            RoleSessionName=f"acm-dns-validation-{certificate_id}"[:64],
        )["Credentials"]
        return boto3.client(
            "route53",
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )

    @staticmethod
    def _build_response(results):
        """
        Build a standardized AWS Lambda response.
        """
        return {"statusCode": 200, "body": json.dumps(results)}


def lambda_handler(event, context):  # pylint: disable=unused-argument
    """
    AWS Lambda handler function to create ACM DNS validation records cross-account.
    """
    handler = AcmDnsValidationHandler()
    return handler.handle(event)
