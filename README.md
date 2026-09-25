MSK_CLUSTER_NAME="cdi-db-infra--msk-cluster--${KAFKA_CONTAINER_ENVIRONMENT}"
echo "Looking up MSK cluster ${MSK_CLUSTER_NAME} in ${REGION}"

CLUSTERS=$(aws kafka list-clusters --region "$REGION" --output json)
KAFKA_MSK_CLUSTER_ARN=$(echo "$CLUSTERS" | jq -r --arg name "$MSK_CLUSTER_NAME" \
	'.ClusterInfoList[] | select(.ClusterName == $name) | .ClusterArn')

if [ -z "$KAFKA_MSK_CLUSTER_ARN" ]; then
	echo "ERROR: no MSK cluster named ${MSK_CLUSTER_NAME}. Found:" >&2
	echo "$CLUSTERS" | jq -r '.ClusterInfoList[].ClusterName' >&2
	exit 1
fi
