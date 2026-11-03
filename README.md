#!/bin/sh
# Start a Kafka Connect worker in distributed mode on ECS Fargate, connecting to MSK over mTLS.
# Derives advertised host, env, region, group.id, storage topics and MSK bootstrap servers from ECS
# task metadata and the AWS CLI. The root filesystem is read-only; /app is the task's own writable volume,
# so the properties are edited in place like the Snowflake worker. The client certificate, chain and encrypted
# key (fetched from Secrets Manager below) are written to CLIENT_PEM, which every ssl.keystore.location points
# to; the key password stays in MSK_CLIENT_KEY_PASSWORD and is read through the env config provider.

set -eu

PROPS=/app/kafka/config/connect-distributed.properties
CLIENT_PEM=/app/certs/client.pem

require() {
	if [ -z "$2" ] || [ "$2" = "null" ]; then
		echo "ERROR: could not resolve $1" >&2
		exit 1
	fi
}

# Print one key of a secret's JSON value.
secret_key() {
	printf '%s' "$1" | jq -r --arg k "$2" '.[$k] // empty'
}

set_prop() {
	if grep -q "^$1=" "$PROPS"; then
		sed -i "s|^$1=.*|$1=$2|" "$PROPS"
	else
		echo "$1=$2" >>"$PROPS"
	fi
}

echo "Starting Kafka Connect worker in distributed mode"

CONTAINER_META=$(curl -sf "${ECS_CONTAINER_METADATA_URI_V4}")
TASK_META=$(curl -sf "${ECS_CONTAINER_METADATA_URI_V4}/task")

KAFKA_CONTAINER_HOSTNAME=$(echo "$CONTAINER_META" | jq -r '.Networks[0].IPv4Addresses[0]')
KAFKA_CONTAINER_ENVIRONMENT=$(echo "$CONTAINER_META" | jq -r '.Name' | sed 's/.*-//')
SERVICE_NAME=$(echo "$TASK_META" | jq -r '.Family')
REGION=${AWS_REGION:-$(echo "$TASK_META" | jq -r '.TaskARN' | cut -d: -f4)}

require KAFKA_CONTAINER_HOSTNAME "$KAFKA_CONTAINER_HOSTNAME"
require KAFKA_CONTAINER_ENVIRONMENT "$KAFKA_CONTAINER_ENVIRONMENT"
require SERVICE_NAME "$SERVICE_NAME"
require REGION "$REGION"

MSK_CLIENT_SECRET_NAME="civica-connect/${KAFKA_CONTAINER_ENVIRONMENT}/msk-client-cert"

MSK_CLIENT_SECRET=$(aws secretsmanager get-secret-value --region "$REGION" \
	--secret-id "$MSK_CLIENT_SECRET_NAME" --query SecretString --output text)
require "secret ${MSK_CLIENT_SECRET_NAME}" "$MSK_CLIENT_SECRET"

# The secret holds one PEM (encrypted key, client certificate and CA chain together) plus the key password.
MSK_CLIENT_PEM=$(secret_key "$MSK_CLIENT_SECRET" pem)
MSK_CLIENT_KEY_PASSWORD=$(secret_key "$MSK_CLIENT_SECRET" password)
require "${MSK_CLIENT_SECRET_NAME}:pem" "$MSK_CLIENT_PEM"
require "${MSK_CLIENT_SECRET_NAME}:password" "$MSK_CLIENT_KEY_PASSWORD"

# Written as-is; readable by this user only.
mkdir -p "$(dirname "$CLIENT_PEM")"
(umask 077 && printf '%s\n' "$MSK_CLIENT_PEM" >"$CLIENT_PEM")
export MSK_CLIENT_KEY_PASSWORD

KAFKA_MSK_CLUSTER_ARN=$(aws kafka list-clusters --region "$REGION" |
	jq -r --arg name "cdi-db-infra--msk-cluster--${KAFKA_CONTAINER_ENVIRONMENT}" \
		'.ClusterInfoList[] | select(.ClusterName == $name) | .ClusterArn')
require KAFKA_MSK_CLUSTER_ARN "$KAFKA_MSK_CLUSTER_ARN"

# mTLS listener (port 9094)
KAFKA_MSK_BOOTSTRAP_SERVERS=$(aws kafka get-bootstrap-brokers --region "$REGION" \
	--cluster-arn "$KAFKA_MSK_CLUSTER_ARN" | jq -r '.BootstrapBrokerStringTls')
require KAFKA_MSK_BOOTSTRAP_SERVERS "$KAFKA_MSK_BOOTSTRAP_SERVERS"

set_prop bootstrap.servers "$KAFKA_MSK_BOOTSTRAP_SERVERS"
set_prop rest.advertised.host.name "$KAFKA_CONTAINER_HOSTNAME"
set_prop group.id "$SERVICE_NAME"
set_prop config.storage.topic "${SERVICE_NAME}-configs"
set_prop offset.storage.topic "${SERVICE_NAME}-offsets"
set_prop status.storage.topic "${SERVICE_NAME}-status"

echo "=== Kafka Connect Config ==="
echo "  region:                    ${REGION}"
echo "  environment:               ${KAFKA_CONTAINER_ENVIRONMENT}"
echo "  bootstrap.servers:         ${KAFKA_MSK_BOOTSTRAP_SERVERS}"
echo "  rest.advertised.host.name: ${KAFKA_CONTAINER_HOSTNAME}"
echo "  group.id:                  ${SERVICE_NAME}"
echo "  config.storage.topic:      ${SERVICE_NAME}-configs"
echo "  offset.storage.topic:      ${SERVICE_NAME}-offsets"
echo "  status.storage.topic:      ${SERVICE_NAME}-status"
echo "  msk client secret:         ${MSK_CLIENT_SECRET_NAME} -> ${CLIENT_PEM}"
echo "============================"

# Native libraries (zstd, snappy) extract to java.io.tmpdir, which must be writable (/app is the task volume).
export KAFKA_OPTS="${KAFKA_OPTS:-} -Djava.io.tmpdir=/app/tmp -XX:-UsePerfData"
exec /app/kafka/bin/connect-distributed.sh "$PROPS"
