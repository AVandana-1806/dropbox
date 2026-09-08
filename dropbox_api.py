"""CloudWatch-to-Splunk Firehose Transform
==========================================

Firehose data processor: decompresses CloudWatch Logs batches, flattens
them, and emits one JSON line per log event for the Splunk HEC raw
endpoint. Records that would push the response past Firehose's 6 MB
synchronous-invoke limit are re-ingested into the delivery stream for a
later invocation instead of being lost.

Event selection happens upstream: only the required log groups have
subscription filters, so this processor forwards everything it receives.

Environment variables:
    EMIT_METRICS       "true" to emit per-log-group EMF counters for the
                       ledger reconciler (default: false)
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
logger.setLevel(logging.INFO)
for _noisy in ("boto3", "botocore", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# EMF requires the raw JSON as the entire log line; the default Lambda log
# format would prefix it and break parsing, so use a bare stdout handler.
_metrics_logger = logging.getLogger("emf")
_metrics_handler = logging.StreamHandler(sys.stdout)
_metrics_handler.setFormatter(logging.Formatter("%(message)s"))
_metrics_logger.addHandler(_metrics_handler)
_metrics_logger.setLevel(logging.INFO)
_metrics_logger.propagate = False

METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "LogPipeline")
# Off by default: the per-log-group EventsIn/EventsOut metrics exist for the
# ledger reconciler and cost money as custom metrics. Flip on when (if) the
# ledger components are deployed.
EMIT_METRICS = os.environ.get("EMIT_METRICS", "false").lower() == "true"

MAX_RESPONSE_BYTES = 5_500_000
MAX_BATCH_RECORDS = 500
MAX_BATCH_BYTES = 3_500_000

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

# Built once per container, not per invocation: constructing a client rebuilds
# botocore's metadata and TLS pool, which is wasted work at ~100k invokes/day.
FIREHOSE_CLIENT = boto3.client("firehose")


class CloudWatchBatchTransformer:
    """Decompress and reshape CloudWatch batches into Splunk HEC raw lines."""

    def transform(self, record: dict) -> dict:
        """Turn one Firehose record into a Firehose transformation result."""
        record_id = record["recordId"]
        try:
            envelope = json.loads(gzip.decompress(base64.b64decode(record["data"])))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.error("record %s unparseable: %s", record_id, exc)
            return {"recordId": record_id, "result": "ProcessingFailed"}

        if envelope.get("messageType") != "DATA_MESSAGE":
            return {"recordId": record_id, "result": "Dropped"}

        try:
            events = [
                self._to_hec_event(envelope, event)
                for event in envelope["logEvents"]
            ]
            stats = {
                "log_group": envelope["logGroup"],
                "events_in": len(envelope["logEvents"]),
                "events_out": len(events),
            }
        except (KeyError, TypeError) as exc:
            logger.error("record %s has malformed envelope: %s", record_id, exc)
            return {"recordId": record_id, "result": "ProcessingFailed"}
        if not events:
            return {"recordId": record_id, "result": "Dropped", "_stats": stats}

        payload = "".join(events).encode()
        return {
            "recordId": record_id,
            "result": "Ok",
            "data": base64.b64encode(payload).decode(),
            "_stats": stats,
        }

    @staticmethod
    def split(record: dict) -> list[bytes]:
        """Chunk an oversized batch into re-ingestable gzipped envelopes."""
        envelope = json.loads(gzip.decompress(base64.b64decode(record["data"])))
        events = envelope["logEvents"]
        if len(events) < 2:
            return []

        # Chunk by uncompressed size, not by event count: halving a batch whose
        # bulk sits in one huge event would converge on that event, never on a
        # small chunk. Each emitted envelope is verified against Firehose's
        # 1,000 KiB per-record limit before it is returned.
        chunks: list[bytes] = []
        current: list[dict] = []
        size = 0
        for event in events:
            # len() on the encoded message: non-ASCII logs are multi-byte, and
            # the budget is a byte budget.
            event_size = (
                len(str(event.get("message", "")).encode()) + EVENT_OVERHEAD_BYTES
            )
            if current and size + event_size > REINGEST_CHUNK_BYTES:
                chunks.append(CloudWatchBatchTransformer._envelope(envelope, current))
                current, size = [], 0
            current.append(event)
            size += event_size
        if current:
            chunks.append(CloudWatchBatchTransformer._envelope(envelope, current))

        if any(len(chunk) > FIREHOSE_MAX_RECORD_BYTES for chunk in chunks):
            # A single event is too large to re-ingest even alone; the caller
            # marks the record ProcessingFailed so Firehose preserves it in S3
            # rather than looping on a batch Firehose will always reject.
            logger.error(
                "record %s has an event too large to re-ingest", record["recordId"]
            )
            return []
        return chunks

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
        try:
            response = self._firehose.put_record_batch(
                DeliveryStreamName=self._stream,
                Records=[{"Data": p} for p in batch],
            )
        except ClientError as exc:
            raise RuntimeError(f"re-ingestion to {self._stream} failed") from exc
        failed = response.get("FailedPutCount", 0)
        if failed:
            raise RuntimeError(
                f"{failed}/{len(batch)} re-ingested records rejected by {self._stream}"
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
                    {"Name": "DeliveredBytes", "Unit": "Bytes"},
                ],
            }],
        },
        "Reingested": counts["Reingested"],
        "ProcessingFailed": counts["ProcessingFailed"],
        "DeliveredBytes": counts["bytes"],
    }))


def _emit_metrics(stats: dict) -> None:
    """Publish per-log-group EMF counters for the ledger reconciler."""
    timestamp = int(time.time() * 1000)
    for log_group, (events_in, events_out) in stats.items():
        _metrics_logger.info(json.dumps({
            "_aws": {
                "Timestamp": timestamp,
                "CloudWatchMetrics": [{
                    "Namespace": METRIC_NAMESPACE,
                    "Dimensions": [["LogGroup"]],
                    "Metrics": [
                        {"Name": "EventsIn", "Unit": "Count"},
                        {"Name": "EventsOut", "Unit": "Count"},
                    ],
                }],
            },
            "LogGroup": log_group,
            "EventsIn": events_in,
            "EventsOut": events_out,
        }))


def handler(event: dict, context: object) -> dict:
    """Firehose transformation entry point."""
    reingester = Reingester(event)
    results: list[dict] = []
    total = 0
    counts = {"Ok": 0, "Dropped": 0, "ProcessingFailed": 0, "Reingested": 0}
    group_stats: dict = {}

    def tally(stats: dict) -> None:
        entry = group_stats.setdefault(stats["log_group"], [0, 0])
        entry[0] += stats["events_in"]
        entry[1] += stats["events_out"]

    for record in event["records"]:
        outcome = _TRANSFORMER.transform(record)
        stats = outcome.pop("_stats", None)

        if outcome["result"] != "Ok":
            # Filtered-out batches still entered the pipeline; count them.
            # Control messages and parse failures carry no stats.
            if stats:
                tally(stats)
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
            # Only records kept in THIS invocation count toward metrics;
            # re-ingested ones are counted when they come back around.
            tally(stats)
            total += size
            counts["Ok"] += 1
            results.append(outcome)

    reingester.flush()
    counts["bytes"] = total
    _emit_summary(counts)
    if EMIT_METRICS:
        _emit_metrics(group_stats)
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
