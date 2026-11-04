# Shared Kafka Connect worker image; build once per connector:
#   Civica:    docker build -f docker/Dockerfile --build-arg CONNECTOR=civica --build-arg VERSION=<x.y.z> \
#                --build-context connector=<repo B> -t <ecr>/civica-connect:<x.y.z> .
#   Snowflake: docker build -f docker/Dockerfile --build-arg CONNECTOR=snowflake \
#                -t <ecr>/snowflake-connect:<x.y.z> .
ARG CONNECTOR=civica

# Civica: build the connector JAR from repo B.
FROM maven:3.9-eclipse-temurin-21 AS plugin-civica
ARG VERSION=1.0.0-SNAPSHOT
WORKDIR /build

# Dependencies first, so they are cached until pom.xml changes.
COPY --from=connector pom.xml .
RUN mvn -B -q -Dversion=${VERSION} dependency:go-offline

COPY --from=connector src ./src
RUN mvn -B -Dversion=${VERSION} package \
    && mkdir -p /plugins/cares-civica-connector \
    && cp target/cares-civica-connector-*.jar /plugins/cares-civica-connector/

# Snowflake: download the connector JARs (replace with your current Snowflake Maven step).
FROM maven:3.9-eclipse-temurin-21 AS plugin-snowflake
WORKDIR /build
COPY docker/snowflake-pom.xml pom.xml
RUN mvn -B -q dependency:copy-dependencies -DoutputDirectory=/plugins/snowflake-kafka-connector

# Only the selected connector's stage is built.
FROM plugin-${CONNECTOR} AS plugin

FROM amazoncorretto:21-al2023-headless

ARG SCALA_VERSION=2.13
ARG KAFKA_VERSION=4.2.0

RUN dnf install -y awscli-2 jq tar gzip \
    && dnf clean all \
    && rm -rf /var/cache/dnf

RUN mkdir -p /app/kafka /app/tmp \
    && curl -fsSL https://archive.apache.org/dist/kafka/${KAFKA_VERSION}/kafka_${SCALA_VERSION}-${KAFKA_VERSION}.tgz \
       | tar -xz -C /app/kafka --strip-components=1

# Connector JARs from the selected plugin stage.
COPY --from=plugin /plugins/ /app/plugins/

COPY docker/connect-distributed.properties /app/kafka/config/connect-distributed.properties
# Console-only logging (CloudWatch), so no connect.log builds up inside the task.
COPY docker/connect-log4j2.yaml /app/kafka/config/connect-log4j2.yaml
COPY docker/start-kafka-connect.sh /app/start-kafka-connect.sh
RUN chmod +x /app/start-kafka-connect.sh

ENV KAFKA_HEAP_OPTS="-XX:InitialRAMPercentage=25.0 -XX:MaxRAMPercentage=75.0"

# The task runs with readonlyRootFilesystem and mounts a volume at /app; ECS copies the image's /app into
# it at task start, so /app is the task's own writable copy. Must come after every step that writes /app.
VOLUME ["/app"]

EXPOSE 8083
ENTRYPOINT ["/app/start-kafka-connect.sh"]
