#!/bin/sh
# Start a Kafka Connect worker in distributed mode on ECS Fargate.
# Derives advertised host, env, group.id and storage topics from ECS task metadata.

set -eu

PROPS=/app/kafka/config/connect-distributed.properties

require() {
  if [ -z "$2" ] || [ "$2" = "null" ]; then
    echo "ERROR: could not resolve $1" >&2
    exit 1
  fi
}

set_prop() {
  if grep -q "^$1=" "$PROPS"; then
    sed -i "s|^$1=.*|$1=$2|" "$PROPS"
  else
    echo "$1=$2" >> "$PROPS"
  fi
}

echo "Starting Kafka Connect worker in distributed mode"

# Container endpoint = this container (IP, name); /task endpoint = task definition (family)
CONTAINER_META=$(curl -sf "${ECS_CONTAINER_METADATA_URI_V4}")
TASK_META=$(curl -sf "${ECS_CONTAINER_METADATA_URI_V4}/task")

KAFKA_CONTAINER_HOSTNAME=$(echo "$CONTAINER_META" | jq -r '.Networks[0].IPv4Addresses[0]')
KAFKA_CONTAINER_ENVIRONMENT=$(echo "$CONTAINER_META" | jq -r '.Name' | sed 's/.*-//')
# e.g. "kafka-connector-cares300" -> used as-is for group.id and topic prefix
SERVICE_NAME=$(echo "$TASK_META" | jq -r '.Family')
# Fargate sets AWS_REGION; fall back to the region field of the task ARN
REGION=${AWS_REGION:-$(echo "$TASK_META" | jq -r '.TaskARN' | cut -d: -f4)}

require KAFKA_CONTAINER_HOSTNAME "$KAFKA_CONTAINER_HOSTNAME"
require KAFKA_CONTAINER_ENVIRONMENT "$KAFKA_CONTAINER_ENVIRONMENT"
require SERVICE_NAME "$SERVICE_NAME"
require REGION "$REGION"

KAFKA_MSK_CLUSTER_ARN=$(aws kafka list-clusters --region "$REGION" \
  | jq -r --arg name "cdi-db-infra--msk-cluster--${KAFKA_CONTAINER_ENVIRONMENT}" \
      '.ClusterInfoList[] | select(.ClusterName == $name) | .ClusterArn')
require KAFKA_MSK_CLUSTER_ARN "$KAFKA_MSK_CLUSTER_ARN"

KAFKA_MSK_BOOTSTRAP_SERVERS=$(aws kafka get-bootstrap-brokers --region "$REGION" \
  --cluster-arn "$KAFKA_MSK_CLUSTER_ARN" | jq -r '.BootstrapBrokerString')
require KAFKA_MSK_BOOTSTRAP_SERVERS "$KAFKA_MSK_BOOTSTRAP_SERVERS"

set_prop bootstrap.servers           "$KAFKA_MSK_BOOTSTRAP_SERVERS"
set_prop rest.advertised.host.name   "$KAFKA_CONTAINER_HOSTNAME"
set_prop group.id                    "$SERVICE_NAME"
set_prop config.storage.topic        "${SERVICE_NAME}-configs"
set_prop offset.storage.topic        "${SERVICE_NAME}-offsets"
set_prop status.storage.topic        "${SERVICE_NAME}-status"

echo "=== Kafka Connect Config ==="
echo "  region:                    ${REGION}"
echo "  environment:               ${KAFKA_CONTAINER_ENVIRONMENT}"
echo "  bootstrap.servers:         ${KAFKA_MSK_BOOTSTRAP_SERVERS}"
echo "  rest.advertised.host.name: ${KAFKA_CONTAINER_HOSTNAME}"
echo "  group.id:                  ${SERVICE_NAME}"
echo "  config.storage.topic:      ${SERVICE_NAME}-configs"
echo "  offset.storage.topic:      ${SERVICE_NAME}-offsets"
echo "  status.storage.topic:      ${SERVICE_NAME}-status"
echo "============================"

# exec gosu "$KAFKA_USER" /app/kafka/bin/connect-distributed.sh "$PROPS"  # if stepping down from root
exec /app/kafka/bin/connect-distributed.sh "$PROPS"
