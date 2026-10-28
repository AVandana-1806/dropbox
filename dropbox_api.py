"""
MSK Connect Connector Deployment Script.

This script deploys a custom plugin JAR and a sink connector to an MSK Connect cluster.

Usage:
  python3 deploy_msk_connector.py --app APP_NAME --env ENV --version VERSION --jar JAR_PATH \
    --env-properties ENV_PROPERTIES --secure-properties SECURE_PROPERTIES

Arguments:
    --app APP          Base name of the application (e.g., 'myapp').
    --env ENV          Target environment (e.g., 'dev', 'qa', 'prod').
    --version VERSION  Application version (e.g., '1.0.0').
    --jar JAR_PATH      Path to the Mule application JAR file.
    --env-properties ENV_PROPERTIES  Path to the non-secure properties file.
    --secure-properties SECURE_PROPERTIES  Path to the secure properties file.

Keys prefixed with 'worker.config.' in the env properties file are stripped of the
prefix and published as an MSK Connect worker configuration (e.g.
'worker.config.consumer.max.poll.records=100'). Worker configurations are immutable,
so a change creates a new one and recreates the connector under the same name.

"""

import argparse
import base64
import hashlib
import logging
import sys
import time
from dataclasses import dataclass
import boto3
from botocore.exceptions import ClientError

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
for _logger in ("boto3", "botocore"):
    logging.getLogger(_logger).setLevel(logging.WARNING)
log = logging.getLogger("msk_connector_deploy")

WAIT_POLL_SECONDS = 15
WAIT_TIMEOUT_SECONDS = 900

INFRA_KEYS = frozenset(
    {
        "kafka.connect.version",
        "mcu.count",
        "min.worker.count",
        "max.worker.count",
        "tls.bootstrap.servers",
        "subnet.ids",
        "security.group.ids",
        "service.execution.role.arn",
        "log.group.name",
    }
)
WORKER_PREFIX = "worker.config."
WORKER_REQUIRED_KEYS = ("key.converter", "value.converter")


def read_properties(path: str) -> dict:
    """Read a flat key=value properties file, skipping blanks and '#' comments."""
    props = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, _, value = line.partition("=")
                props[key.strip()] = value.strip()
    return props


@dataclass(frozen=True)
class DeployConfig:
    """
    Resolved deployment configuration for one environment.
    """

    environment: str
    plugin_name: str
    connector_name: str
    plugin_location: dict
    connector_request: dict
    worker_properties: dict

    @property
    def worker_config_name(self) -> str:
        """Return a content-addressed worker configuration name."""
        digest = hashlib.sha256(self.worker_properties_content.encode()).hexdigest()
        return f"{self.connector_name}-{digest[:10]}"

    @property
    def worker_properties_content(self) -> str:
        """Render worker properties as a connect-distributed.properties file."""
        return "\n".join(f"{k}={v}" for k, v in sorted(self.worker_properties.items()))

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "DeployConfig":
        """
        Build config and API request payloads from CLI args and properties files.
        """
        bucket, _, key = args.jar.removeprefix("s3://").partition("/")
        env = read_properties(args.env_properties)
        secure = read_properties(args.secure_properties)

        worker = {
            k.removeprefix(WORKER_PREFIX): v
            for k, v in env.items()
            if k.startswith(WORKER_PREFIX)
        }
        missing = [k for k in WORKER_REQUIRED_KEYS if worker and k not in worker]
        if missing:
            raise ValueError(
                f"worker configuration in {args.env_properties} is missing "
                f"required keys: {[WORKER_PREFIX + k for k in missing]}"
            )
        connector_props = {
            k: v
            for k, v in env.items()
            if k not in INFRA_KEYS and not k.startswith(WORKER_PREFIX)
        }

        overlap = set(connector_props) & set(secure)
        if overlap:
            raise ValueError(
                f"keys present in both {args.env_properties} and "
                f"{args.secure_properties}: {sorted(overlap)}"
            )

        connector_name = f"{args.app}-{args.env}"
        return cls(
            environment=args.env,
            plugin_name=f"{args.app}-{args.version}".replace(".", "-"),
            connector_name=connector_name,
            plugin_location={
                "s3Location": {"bucketArn": f"arn:aws:s3:::{bucket}", "fileKey": key}
            },
            connector_request={
                "connectorName": connector_name,
                "kafkaConnectVersion": env["kafka.connect.version"],
                "capacity": {
                    "autoScaling": {
                        "mcuCount": int(env["mcu.count"]),
                        "minWorkerCount": int(env["min.worker.count"]),
                        "maxWorkerCount": int(env["max.worker.count"]),
                        "scaleInPolicy": {"cpuUtilizationPercentage": 20},
                        "scaleOutPolicy": {"cpuUtilizationPercentage": 80},
                    }
                },
                "connectorConfiguration": connector_props | secure,
                "kafkaCluster": {
                    "apacheKafkaCluster": {
                        "bootstrapServers": env["tls.bootstrap.servers"],
                        "vpc": {
                            "subnets": [
                                s.strip() for s in env["subnet.ids"].split(",")
                            ],
                            "securityGroups": [
                                s.strip() for s in env["security.group.ids"].split(",")
                            ],
                        },
                    }
                },
                "kafkaClusterEncryptionInTransit": {"encryptionType": "TLS"},
                "kafkaClusterClientAuthentication": {"authenticationType": "NONE"},
                "serviceExecutionRoleArn": env["service.execution.role.arn"],
                "logDelivery": {
                    "workerLogDelivery": {
                        "cloudWatchLogs": {
                            "enabled": True,
                            "logGroup": env["log.group.name"],
                        }
                    }
                },
            },
            worker_properties=worker,
        )


class ConnectorDeployer:
    """
    Manages the shared custom plugin and the per-environment connector.
    """

    def __init__(self, config: DeployConfig):
        self.config = config
        self.client = boto3.client("kafkaconnect")

    def ensure_plugin(self) -> tuple[str, int]:
        """Create or reuse the shared plugin for this version, returning (arn, revision)."""
        name = self.config.plugin_name
        pages = self.client.get_paginator("list_custom_plugins").paginate(
            namePrefix=name
        )
        existing = next(
            (
                p
                for page in pages
                for p in page.get("customPlugins", [])
                if p["name"] == name
            ),
            None,
        )
        if existing:
            log.info("Plugin '%s' already exists — reusing.", name)
            arn = existing["customPluginArn"]
        else:
            log.info("Creating plugin '%s' from %s", name, self.config.plugin_location)
            arn = self.client.create_custom_plugin(
                name=name, contentType="JAR", location=self.config.plugin_location
            )["customPluginArn"]

        detail = self._wait(
            lambda: self.client.describe_custom_plugin(customPluginArn=arn),
            state_key="customPluginState",
            target={"ACTIVE"},
            failure={"CREATE_FAILED"},
            what=f"plugin {name}",
        )
        return arn, detail["latestRevision"]["revision"]

    def ensure_worker_config(self) -> dict | None:
        """Create or reuse the worker configuration, returning its connector reference."""
        if not self.config.worker_properties:
            log.info("No worker.config.* properties — using the MSK Connect default.")
            return None

        name = self.config.worker_config_name
        pages = self.client.get_paginator("list_worker_configurations").paginate(
            namePrefix=name
        )
        existing = next(
            (
                w
                for page in pages
                for w in page.get("workerConfigurations", [])
                if w["name"] == name
            ),
            None,
        )
        if existing:
            log.info("Worker configuration '%s' already exists — reusing.", name)
        else:
            log.info(
                "Creating worker configuration '%s':\n%s",
                name,
                self.config.worker_properties_content,
            )
            existing = self.client.create_worker_configuration(
                name=name,
                propertiesFileContent=base64.b64encode(
                    self.config.worker_properties_content.encode()
                ).decode(),
            )
        return {
            "workerConfigurationArn": existing["workerConfigurationArn"],
            "revision": existing["latestRevision"]["revision"],
        }

    def deploy_connector(
        self, plugin_arn: str, plugin_revision: int, worker_config: dict | None
    ) -> str:
        """
        Create the connector, update it in place, or recreate it on plugin or worker config change.
        """
        name = self.config.connector_name
        pages = self.client.get_paginator("list_connectors").paginate(
            connectorNamePrefix=name
        )
        existing = next(
            (
                c
                for page in pages
                for c in page.get("connectors", [])
                if c["connectorName"] == name
            ),
            None,
        )

        if existing is None:
            return self._create_connector(plugin_arn, plugin_revision, worker_config)

        arn = existing["connectorArn"]
        detail = self.client.describe_connector(connectorArn=arn)
        current_plugin_arn = detail["plugins"][0]["customPlugin"]["customPluginArn"]
        current_worker_arn = detail.get("workerConfiguration", {}).get(
            "workerConfigurationArn"
        )
        desired_worker_arn = worker_config and worker_config["workerConfigurationArn"]

        reasons = [
            reason
            for reason, changed in (
                ("plugin", current_plugin_arn != plugin_arn),
                ("worker configuration", current_worker_arn != desired_worker_arn),
            )
            if changed
        ]
        if reasons:
            log.info(
                "Connector '%s' has a different %s — deleting and recreating.",
                name,
                " and ".join(reasons),
            )
            self.client.delete_connector(
                connectorArn=arn, currentVersion=detail["currentVersion"]
            )
            self._wait_deleted(arn)
            return self._create_connector(plugin_arn, plugin_revision, worker_config)

        desired_config = self.config.connector_request["connectorConfiguration"]
        if detail["connectorConfiguration"] == desired_config:
            log.info("Connector '%s' already has the desired config — no action.", name)
            return arn

        log.info("Same plugin — updating connector '%s' config in place.", name)
        self.client.update_connector(
            connectorArn=arn,
            currentVersion=detail["currentVersion"],
            connectorConfiguration=self.config.connector_request[
                "connectorConfiguration"
            ],
        )
        self._wait_running(arn)
        return arn

    def _create_connector(
        self, plugin_arn: str, plugin_revision: int, worker_config: dict | None
    ) -> str:
        log.info("Creating connector '%s'...", self.config.connector_name)
        request = self.config.connector_request | {
            "plugins": [
                {
                    "customPlugin": {
                        "customPluginArn": plugin_arn,
                        "revision": plugin_revision,
                    }
                }
            ]
        }
        if worker_config:
            request["workerConfiguration"] = worker_config
        arn = self.client.create_connector(**request)["connectorArn"]
        self._wait_running(arn)
        return arn

    def _wait(
        self, describe, state_key: str, target: set, failure: set, what: str
    ) -> dict:
        start = time.monotonic()
        while True:
            detail = describe()
            state = detail[state_key]
            if state in target:
                log.info("%s reached state %s.", what, state)
                return detail
            if state in failure:
                raise RuntimeError(
                    f"{what} entered {state}: {detail.get('stateDescription', 'no details')}"
                )
            if time.monotonic() - start > WAIT_TIMEOUT_SECONDS:
                raise TimeoutError(
                    f"timed out waiting for {what} (last state: {state})"
                )
            time.sleep(WAIT_POLL_SECONDS)

    def _wait_running(self, arn: str):
        self._wait(
            lambda: self.client.describe_connector(connectorArn=arn),
            state_key="connectorState",
            target={"RUNNING"},
            failure={"FAILED"},
            what=f"connector {self.config.connector_name}",
        )

    def _wait_deleted(self, arn: str):
        start = time.monotonic()
        while True:
            try:
                self.client.describe_connector(connectorArn=arn)
            except ClientError as e:
                if e.response["Error"]["Code"] == "NotFoundException":
                    log.info("Connector deleted.")
                    return
                raise
            if time.monotonic() - start > WAIT_TIMEOUT_SECONDS:
                raise TimeoutError("timed out waiting for connector deletion")
            time.sleep(WAIT_POLL_SECONDS)


def parse_args():
    """
    Parses command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Deploy plugin and sink connector to MSK Connect"
    )
    parser.add_argument("-a", "--app", required=True, help="Application name")
    parser.add_argument("-e", "--env", required=True, help="Environment (dev/qa/prod)")
    parser.add_argument("-v", "--version", required=True, help="Application version")
    parser.add_argument("-j", "--jar", required=True, help="s3 URI of the plugin JAR")
    parser.add_argument(
        "-p", "--env-properties", required=True, help="non-secure properties file"
    )
    parser.add_argument(
        "-s", "--secure-properties", required=True, help="Secure properties file"
    )
    return parser.parse_args()


def main():
    """
    Main entry point.
    """
    try:
        config = DeployConfig.from_args(parse_args())
    except (OSError, ValueError, KeyError) as e:
        log.exception("Config error: %s", e)
        sys.exit(1)

    log.info(
        "Deploying to '%s' (plugin=%s, connector=%s)",
        config.environment,
        config.plugin_name,
        config.connector_name,
    )

    deployer = ConnectorDeployer(config)
    try:
        plugin_arn, plugin_revision = deployer.ensure_plugin()
        worker_config = deployer.ensure_worker_config()
        connector_arn = deployer.deploy_connector(
            plugin_arn, plugin_revision, worker_config
        )
    except (ClientError, RuntimeError, TimeoutError) as e:
        log.exception("Deployment failed: %s", e)
        sys.exit(1)

    log.info("Deployment complete: %s", connector_arn)


if __name__ == "__main__":
    main()
