"""CloudWatch-to-Splunk Firehose Transform
==========================================

Firehose data processor: decompresses CloudWatch Logs batches, flattens
them, and emits one JSON line per log event for the Splunk HEC raw
endpoint. Records that would push the response past Firehose's 6 MB
synchronous-invoke limit are re-ingested into the delivery stream for a
later invocation instead of being lost.

Event selection happens upstream: only the required log groups have
subscription filters, so this processor forwards everything it receives.

Emits Reingested / ProcessingFailed / DeliveredBytes as EMF counters for
the pipeline alarms.
"""

import base64
import gzip
import json
import logging
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
_metrics_logger = logging.getLogger("emf")


def _configure_logging() -> None:
    logger.setLevel(logging.INFO)
    for name in ("boto3", "botocore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)

    if _metrics_logger.handlers:
        return

    # EMF requires the raw JSON as the entire log line; the default Lambda log
    # format would prefix it and break parsing, so use a bare stdout handler.
    metrics_handler = logging.StreamHandler(sys.stdout)
    metrics_handler.setFormatter(logging.Formatter("%(message)s"))
    _metrics_logger.addHandler(metrics_handler)
    _metrics_logger.setLevel(logging.INFO)
    _metrics_logger.propagate = False


_configure_logging()

METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "LogPipeline")

MAX_RESPONSE_BYTES = 5_500_000
MAX_BATCH_RECORDS = 500
MAX_BATCH_BYTES = 3_500_000
PUT_BATCH_ATTEMPTS = 3
PUT_BATCH_BACKOFF_SECONDS = 0.2

# Firehose rejects any single record over 1,000 KiB. Chunk well under it:
# the budget is measured on uncompressed message bytes, and gzip only ever
# shrinks log text, so a 700 KB chunk is comfortably inside the limit.
FIREHOSE_MAX_RECORD_BYTES = 1_024_000
REINGEST_CHUNK_BYTES = 700_000
# Measured: a zero-length event serializes to ~110 bytes of envelope JSON
# (56-char id, 13-digit timestamp, keys and punctuation). Rounded up, since
# underestimating here would let a chunk exceed the record limit and force an
# otherwise splittable record to S3.
EVENT_OVERHEAD_BYTES = 128

# A chunk must also stay small enough that re-processing it fits the response
# budget, or it would be split into itself and re-ingested forever. Every HEC
# line repeats the log group, stream and filter names, so a batch of many tiny
# events inflates several times over; budget on the transformed size, allowing
# for base64's 4/3 expansion on top.
HEC_LINE_OVERHEAD_BYTES = 96
TRANSFORMED_CHUNK_BYTES = 3_000_000

# Built once per container, not per invocation: constructing a client rebuilds
# botocore's metadata and TLS pool, which is wasted work at ~100k invokes/day.
FIREHOSE_CLIENT = boto3.client("firehose")


class CloudWatchBatchTransformer:
    """Decompress and reshape CloudWatch batches into Splunk HEC raw lines."""

    def transform(self, record: dict) -> dict:
        """Turn one Firehose record into a Firehose transformation result."""
        record_id = record.get("recordId", "")
        try:
            envelope = json.loads(gzip.decompress(base64.b64decode(record["data"])))
        except (KeyError, OSError, ValueError) as exc:
            logger.error("record %s unparseable: %s", record_id, exc)
            return {"recordId": record_id, "result": "ProcessingFailed"}

        # Valid JSON that is not an object (a bare list or string) would blow up
        # on the attribute access below; contain it to this record.
        if not isinstance(envelope, dict):
            logger.error(
                "record %s is not a CloudWatch envelope: %s",
                record_id,
                type(envelope).__name__,
            )
            return {"recordId": record_id, "result": "ProcessingFailed"}

        if envelope.get("messageType") != "DATA_MESSAGE":
            return {"recordId": record_id, "result": "Dropped"}

        try:
            events = [
                self._to_hec_event(envelope, event)
                for event in envelope["logEvents"]
            ]
        except (KeyError, TypeError) as exc:
            logger.error("record %s has malformed envelope: %s", record_id, exc)
            return {"recordId": record_id, "result": "ProcessingFailed"}
        if not events:
            return {"recordId": record_id, "result": "Dropped"}

        payload = "".join(events).encode()
        return {
            "recordId": record_id,
            "result": "Ok",
            "data": base64.b64encode(payload).decode(),
        }

    @staticmethod
    def split(record: dict) -> list[bytes]:
        """Chunk an oversized batch into smaller re-ingestable gzipped envelopes."""
        envelope = json.loads(gzip.decompress(base64.b64decode(record["data"])))
        events = envelope["logEvents"]
        if len(events) < 2:
            return []

        groups = CloudWatchBatchTransformer._group(envelope, events)
        if len(groups) == 1:
            # One group means no progress: the chunk would be the input, so it
            # would come back oversized and split into itself forever. Halve by
            # count instead - each pass halves again until the pieces fit.
            middle = len(events) // 2
            groups = [events[:middle], events[middle:]]

        chunks = [
            CloudWatchBatchTransformer._envelope(envelope, group) for group in groups
        ]
        if any(len(chunk) > FIREHOSE_MAX_RECORD_BYTES for chunk in chunks):
            # An event is too large to re-ingest even alone; the caller marks
            # the record ProcessingFailed so Firehose preserves it in S3 rather
            # than looping on a batch Firehose will always reject.
            logger.error(
                "record %s has an event too large to re-ingest", record["recordId"]
            )
            return []
        return chunks

    @staticmethod
    def _group(envelope: dict, events: list[dict]) -> list[list[dict]]:
        # Two budgets, whichever binds first: raw envelope bytes keep the
        # gzipped record under Firehose's per-record limit, transformed bytes
        # keep the re-processed result under the response limit. Sizes are
        # measured on encoded bytes - non-ASCII logs are multi-byte.
        hec_overhead = (
            HEC_LINE_OVERHEAD_BYTES
            + len(str(envelope.get("logGroup", "")).encode())
            + len(str(envelope.get("logStream", "")).encode())
            + len(",".join(envelope.get("subscriptionFilters", [])).encode())
        )
        groups: list[list[dict]] = []
        current: list[dict] = []
        raw = transformed = 0
        for event in events:
            message = len(str(event.get("message", "")).encode())
            event_raw = message + EVENT_OVERHEAD_BYTES
            event_transformed = message + hec_overhead
            over_raw = raw + event_raw > REINGEST_CHUNK_BYTES
            over_transformed = (
                transformed + event_transformed > TRANSFORMED_CHUNK_BYTES
            )
            if current and (over_raw or over_transformed):
                groups.append(current)
                current, raw, transformed = [], 0, 0
            current.append(event)
            raw += event_raw
            transformed += event_transformed
        if current:
            groups.append(current)
        return groups

    @staticmethod
    def _envelope(envelope: dict, events: list[dict]) -> bytes:
        return gzip.compress(json.dumps({**envelope, "logEvents": events}).encode())

    @staticmethod
    def _to_hec_event(envelope: dict, event: dict) -> str:
        # Firehose delivers to HEC's /raw endpoint, so this whole object is
        # the Splunk event and every key is search-time extractable (JSON
        # sourcetype). The shape matches the previous pipeline exactly so
        # existing props.conf / searches keep working. time stays in
        # CloudWatch milliseconds; Splunk parses it via TIME_PREFIX/TIME_FORMAT.
        return (
            json.dumps(
                {
                    "time": event["timestamp"],
                    "subscriptionFilter": ",".join(
                        envelope.get("subscriptionFilters", [])
                    ),
                    "LogGroup": envelope["logGroup"],
                    "LogStream": envelope["logStream"],
                    "event": event["message"],
                }
            )
            + "\n"
        )


class Reingester:
    """Re-queue records that could not fit in this invocation's response."""

    # Direct PUT topology only: records go back into the delivery stream
    # itself. If a Kinesis Data Stream is ever put in front of Firehose,
    # this must switch to kinesis:PutRecords on event["sourceKinesisStreamArn"]
    # instead — a KDS-sourced Firehose rejects PutRecordBatch.
    def __init__(self, event: dict) -> None:
        self._firehose = FIREHOSE_CLIENT
        self._stream = event["deliveryStreamArn"].split("/")[-1]
        self._payloads: list[bytes] = []

    def add(self, payload: bytes) -> None:
        """Queue one gzipped envelope for re-ingestion."""
        self._payloads.append(payload)

    def flush(self) -> None:
        """Send queued payloads in size- and count-bounded batches."""
        batch: list[bytes] = []
        size = 0
        for payload in self._payloads:
            over_count = len(batch) == MAX_BATCH_RECORDS
            over_size = size + len(payload) > MAX_BATCH_BYTES
            if batch and (over_count or over_size):
                self._put_batch(batch)
                batch, size = [], 0
            batch.append(payload)
            size += len(payload)
        if batch:
            self._put_batch(batch)
        if self._payloads:
            logger.info(
                "re-ingested %d records to %s",
                len(self._payloads),
                self._stream,
            )

    def _put_batch(self, batch: list[bytes]) -> None:
        # PutRecordBatch can partially succeed. Re-sending the whole batch
        # would duplicate the records that were accepted, so retry only the
        # ones Firehose named in RequestResponses.
        pending = batch
        for attempt in range(PUT_BATCH_ATTEMPTS):
            if attempt:
                time.sleep(PUT_BATCH_BACKOFF_SECONDS * attempt)
            try:
                response = self._firehose.put_record_batch(
                    DeliveryStreamName=self._stream,
                    Records=[{"Data": payload} for payload in pending],
                )
            except ClientError as exc:
                raise RuntimeError(f"re-ingestion to {self._stream} failed") from exc
            if not response.get("FailedPutCount", 0):
                return
            responses = response.get("RequestResponses", [])
            rejected = [
                payload
                for payload, result in zip(pending, responses)
                if result.get("ErrorCode")
            ]
            # A short, missing or inconsistent RequestResponses list gives no
            # way to tell which records landed. Retrying all of them may
            # duplicate; dropping any of them loses data.
            if len(responses) != len(pending) or not rejected:
                rejected = pending
            pending = rejected
            logger.warning(
                "%d/%d re-ingested records rejected by %s, retrying",
                len(pending),
                len(batch),
                self._stream,
            )
        raise RuntimeError(
            f"{len(pending)}/{len(batch)} re-ingested records still rejected "
            f"by {self._stream} after {PUT_BATCH_ATTEMPTS} attempts"
        )


_TRANSFORMER = CloudWatchBatchTransformer()


def _emit_summary(counts: dict) -> None:
    """Publish invocation-level EMF counters (always on; no dimensions)."""
    _metrics_logger.info(json.dumps({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": METRIC_NAMESPACE,
                "Dimensions": [[]],
                "Metrics": [
                    {"Name": "Reingested", "Unit": "Count"},
                    {"Name": "ProcessingFailed", "Unit": "Count"},
                    {"Name": "Dropped", "Unit": "Count"},
                    {"Name": "DeliveredBytes", "Unit": "Bytes"},
                ],
            }],
        },
        "Reingested": counts["Reingested"],
        "ProcessingFailed": counts["ProcessingFailed"],
        "Dropped": counts["Dropped"],
        "DeliveredBytes": counts["bytes"],
    }))


def handler(event: dict, context: object) -> dict:
    """Firehose transformation entry point."""
    reingester = Reingester(event)
    results: list[dict] = []
    total = 0
    counts = {"Ok": 0, "Dropped": 0, "ProcessingFailed": 0, "Reingested": 0}

    for record in event["records"]:
        outcome = _TRANSFORMER.transform(record)

        if outcome["result"] != "Ok":
            counts[outcome["result"]] += 1
            results.append(outcome)
            continue

        size = len(outcome["data"]) + len(outcome["recordId"]) + 64
        if size > MAX_RESPONSE_BYTES:
            chunks = _TRANSFORMER.split(record)
            if not chunks:
                counts["ProcessingFailed"] += 1
                results.append(
                    {"recordId": record["recordId"], "result": "ProcessingFailed"}
                )
                continue
            for chunk in chunks:
                reingester.add(chunk)
            counts["Reingested"] += 1
            results.append({"recordId": record["recordId"], "result": "Dropped"})
        elif total + size > MAX_RESPONSE_BYTES:
            reingester.add(base64.b64decode(record["data"]))
            counts["Reingested"] += 1
            results.append({"recordId": record["recordId"], "result": "Dropped"})
        else:
            total += size
            counts["Ok"] += 1
            results.append(outcome)

    reingester.flush()
    counts["bytes"] = total
    _emit_summary(counts)
    logger.info(
        "records=%d ok=%d dropped=%d failed=%d reingested=%d bytes=%d",
        len(event["records"]),
        counts["Ok"],
        counts["Dropped"],
        counts["ProcessingFailed"],
        counts["Reingested"],
        total,
    )
    return {"records": results}
