# ---- Stage 1: download connector + its runtime deps ----
FROM maven:3.9-eclipse-temurin-21 AS deps

ARG SF_CONNECTOR_VERSION=4.0.0
ARG DEP_PLUGIN=org.apache.maven.plugins:maven-dependency-plugin:3.11.0
ARG OUT=/plugins/snowflake-kafka-connector
ARG M2=/root/.m2/repository

RUN mvn -q -B ${DEP_PLUGIN}:get -Dartifact=com.snowflake:snowflake-kafka-connector:${SF_CONNECTOR_VERSION}:jar \
    && mvn -q -B ${DEP_PLUGIN}:copy-dependencies \
         -f ${M2}/com/snowflake/snowflake-kafka-connector/${SF_CONNECTOR_VERSION}/snowflake-kafka-connector-${SF_CONNECTOR_VERSION}.pom \
         -DincludeScope=runtime \
         -DexcludeGroupIds=org.apache.kafka \
         -DoutputDirectory=${OUT} \
    && cp ${M2}/com/snowflake/snowflake-kafka-connector/${SF_CONNECTOR_VERSION}/snowflake-kafka-connector-${SF_CONNECTOR_VERSION}.jar ${OUT}/

# Only needed for encrypted private keys
RUN mvn -q -B ${DEP_PLUGIN}:copy -Dartifact=org.bouncycastle:bc-fips:2.1.2:jar -DoutputDirectory=${OUT} \
    && mvn -q -B ${DEP_PLUGIN}:copy -Dartifact=org.bouncycastle:bcpkix-fips:2.1.12:jar -DoutputDirectory=${OUT}

# ---- Stage 2: runtime (glibc, required by the snowpipe-streaming native core) ----
FROM amazoncorretto:21-al2023-headless

ARG SCALA_VERSION=2.13
ARG KAFKA_VERSION=3.9.2

RUN dnf install -y awscli-2 jq tar gzip \
    && dnf clean all \
    && rm -rf /var/cache/dnf

RUN mkdir -p /app/kafka \
    && curl -fsSL https://archive.apache.org/dist/kafka/${KAFKA_VERSION}/kafka_${SCALA_VERSION}-${KAFKA_VERSION}.tgz \
       | tar -xz -C /app/kafka --strip-components=1

COPY --from=deps /plugins /app/plugins

COPY connect-distributed.properties /app/kafka/config/connect-distributed.properties
COPY start-kafka-connect.sh /app/start-kafka-connect.sh
RUN chmod +x /app/start-kafka-connect.sh

# Rust core allocates off-heap; leave roughly half the task memory for it
ENV KAFKA_HEAP_OPTS="-XX:InitialRAMPercentage=25.0 -XX:MaxRAMPercentage=50.0"

EXPOSE 8083
ENTRYPOINT ["/app/start-kafka-connect.sh"]
