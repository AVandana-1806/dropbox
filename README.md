# Shared Kafka Connect worker image. One build per connector:
#   docker build -f docker/Dockerfile --build-arg CONNECTOR=<name> --build-context connector=<source> -t <image> .
# docker/<CONNECTOR>/ holds build-plugin.sh, start-kafka-connect.sh, connect-distributed.properties and
# connect-log4j2.yaml. build-plugin.sh builds or downloads the connector's jars into /plugins/<plugin name>/.
# "connector" is the connector's source code (repo B for Civica); a connector with no source code passes its
# own docker/<CONNECTOR> folder.
ARG CONNECTOR

FROM maven:3.9-eclipse-temurin-21 AS plugin
ARG CONNECTOR
ARG VERSION=1.0.0-SNAPSHOT
ENV VERSION=${VERSION}
COPY --from=connector . /src
COPY docker/${CONNECTOR}/ /conf/
# The Maven cache persists between builds, so dependencies are not downloaded every time.
RUN --mount=type=cache,target=/root/.m2 sh /conf/build-plugin.sh

FROM amazoncorretto:21-al2023-headless

ARG CONNECTOR
ARG SCALA_VERSION=2.13
# Match the connect-api version the connector is built against.
ARG KAFKA_VERSION=4.2.0

RUN dnf install -y awscli-2 jq tar gzip \
    && dnf clean all \
    && rm -rf /var/cache/dnf

RUN mkdir -p /app/kafka /app/tmp \
    && curl -fsSL https://archive.apache.org/dist/kafka/${KAFKA_VERSION}/kafka_${SCALA_VERSION}-${KAFKA_VERSION}.tgz \
       | tar -xz -C /app/kafka --strip-components=1

# plugin.path=/app/plugins; each subfolder is one plugin.
COPY --from=plugin /plugins/ /app/plugins/

COPY docker/${CONNECTOR}/connect-distributed.properties /app/kafka/config/connect-distributed.properties
# Console-only logging (CloudWatch), so no connect.log builds up inside the task.
COPY docker/${CONNECTOR}/connect-log4j2.yaml /app/kafka/config/connect-log4j2.yaml
COPY docker/${CONNECTOR}/start-kafka-connect.sh /app/start-kafka-connect.sh
RUN chmod +x /app/start-kafka-connect.sh

ENV KAFKA_HEAP_OPTS="-XX:InitialRAMPercentage=25.0 -XX:MaxRAMPercentage=75.0"

# The task runs with readonlyRootFilesystem and mounts a volume at /app; ECS copies the image's /app into
# it at task start, so /app is the task's own writable copy. Must come after every step that writes /app.
VOLUME ["/app"]

EXPOSE 8083
ENTRYPOINT ["/app/start-kafka-connect.sh"]
