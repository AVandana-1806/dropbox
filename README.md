# Build the connector JAR (same Maven stage pattern as the Snowflake image).
FROM maven:3.9-eclipse-temurin-21 AS build

ARG VERSION=1.0.0-SNAPSHOT
WORKDIR /build

# Dependencies first, so they are cached until pom.xml changes.
COPY pom.xml .
RUN mvn -B -q -Dversion=${VERSION} dependency:go-offline

COPY src ./src
RUN mvn -B -Dversion=${VERSION} package

FROM amazoncorretto:21-al2023-headless

ARG SCALA_VERSION=2.13
# Matches connect-api in pom.xml, so the runtime is what the connector compiles against.
ARG KAFKA_VERSION=4.2.0

RUN dnf install -y awscli-2 jq tar gzip \
    && dnf clean all \
    && rm -rf /var/cache/dnf

RUN mkdir -p /app/kafka /app/tmp \
    && curl -fsSL https://archive.apache.org/dist/kafka/${KAFKA_VERSION}/kafka_${SCALA_VERSION}-${KAFKA_VERSION}.tgz \
       | tar -xz -C /app/kafka --strip-components=1

# Shaded connector JAR from the build stage.
COPY --from=build /build/target/cares-civica-connector-*.jar /app/plugins/cares-civica-connector/

COPY docker/connect-distributed.properties /app/kafka/config/connect-distributed.properties
# Console-only logging (CloudWatch), so no connect.log builds up inside the task.
COPY docker/connect-log4j2.yaml /app/kafka/config/connect-log4j2.yaml
COPY docker/start-kafka-connect.sh /app/start-kafka-connect.sh
RUN chmod +x /app/start-kafka-connect.sh

ENV KAFKA_HEAP_OPTS="-XX:InitialRAMPercentage=25.0 -XX:MaxRAMPercentage=75.0"

# Secrets Manager names read by start-kafka-connect.sh at startup; {env} is replaced with the
# environment taken from the container name (e.g. cares300).
ENV MSK_CLIENT_SECRET_NAME="civica-connect/{env}/msk-client-cert" \
    CONNECTOR_SECRET_NAME="civica-connect/{env}/connector"

# The task runs with readonlyRootFilesystem and mounts a volume at /app; ECS copies the image's /app into
# it at task start, so /app is the task's own writable copy. Must come after every step that writes /app.
VOLUME ["/app"]

EXPOSE 8083
ENTRYPOINT ["/app/start-kafka-connect.sh"]
