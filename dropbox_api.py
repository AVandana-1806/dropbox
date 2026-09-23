"""S3 Failed-Event Replay
=========================

Scheduled recovery Lambda for the CloudWatch-to-Splunk pipeline. Scans the
Firehose failure prefix in the backup bucket, oldest objects first, and
retries the records inside.

Routing is per record, by the errorCode Firehose recorded in the manifest,
not by the object's prefix: Firehose writes both failure types under a single
prefix unless error_output_prefix keeps the !{firehose:error-output-type}
variable, and the errorCode is authoritative either way.

    Splunk.*  the record was already transformed and Splunk rejected it
              -> POST straight to the HEC raw endpoint
    Lambda.*  the transform errored, was throttled, or never ran
              -> put back into Firehose to pass through the transform

Outcomes per object:
    replayed/    every record was accepted; object moved here (audit trail)
    quarantine/  a permanent failure was seen (HEC 4xx, or an errorCode
                 meaning the transform rejected the record itself); object
                 moved here for a human - retrying would loop forever
    (stays put)  a transient failure; retried on the next scheduled run

Environment variables:
    BUCKET_NAME            backup bucket (required)
    HEC_ENDPOINT           https://splunk-host:8088 (required)
    HEC_SECRET_ARN         Secrets Manager ARN holding the HEC token, as a
                           plain string or JSON with a "token" key (required)
    DELIVERY_STREAM_NAME   Firehose stream for re-ingestion (required)
    FAILED_PREFIX          default: failed-events/
    REPLAYED_PREFIX        default: replayed/
    QUARANTINE_PREFIX      default: quarantine/
    PERMANENT_ERROR_CODES  comma-separated errorCodes that mean the transform
                           rejected the record; quarantined, never re-ingested
                           (default: Lambda.ProcessingFailedStatus)
    MAX_OBJECTS_PER_RUN    default: 200
    MIN_AGE_MINUTES        default: 15 (leave room for Firehose's own retries)
"""

import base64
import gzip
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import boto3
import urllib3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logger.setLevel(logging.INFO)
    for name in ("boto3", "botocore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


_configure_logging()

GZIP_MAGIC = b"\x1f\x8b"
HEC_BATCH_BYTES = 512_000
HEC_RETRY_STATUSES = (408, 429, 500, 502, 503, 504)
FIREHOSE_BATCH_RECORDS = 500
FIREHOSE_BATCH_BYTES = 3_500_000
TIME_GUARD_MS = 30_000
SPLUNK_ERROR_PREFIX = "Splunk."


class PermanentRejection(RuntimeError):
    """The destination rejected the data itself; retrying cannot succeed."""


@dataclass
class ReplayConfig:
    """Runtime configuration read from environment variables."""

    bucket: str
    failed_prefix: str
    replayed_prefix: str
    quarantine_prefix: str
    permanent_error_codes: frozenset[str]
    hec_endpoint: str
    hec_secret_arn: str
    delivery_stream: str
    max_objects: int
    min_age_minutes: int

    @classmethod
    def from_env(cls) -> "ReplayConfig":
        """Build config from the Lambda environment."""
        codes = os.environ.get("PERMANENT_ERROR_CODES",
                               "Lambda.ProcessingFailedStatus")
        return cls(
            bucket=os.environ["BUCKET_NAME"],
            failed_prefix=os.environ.get("FAILED_PREFIX", "failed-events/"),
            replayed_prefix=os.environ.get("REPLAYED_PREFIX", "replayed/"),
            quarantine_prefix=os.environ.get("QUARANTINE_PREFIX", "quarantine/"),
            permanent_error_codes=frozenset(
                code.strip() for code in codes.split(",") if code.strip()
            ),
            hec_endpoint=os.environ["HEC_ENDPOINT"].rstrip("/"),
            hec_secret_arn=os.environ["HEC_SECRET_ARN"],
            delivery_stream=os.environ["DELIVERY_STREAM_NAME"],
            max_objects=int(os.environ.get("MAX_OBJECTS_PER_RUN", "200")),
            min_age_minutes=int(os.environ.get("MIN_AGE_MINUTES", "15")),
        )


class FailedObjectStore:
    """List, read, and relocate failed-event objects in the backup bucket."""

    def __init__(self, config: ReplayConfig, s3_client) -> None:
        self._config = config
        self._s3 = s3_client

    def eligible_keys(self, limit: int) -> list[str]:
        """Return replayable keys under the failure prefix, oldest first."""
        if limit <= 0:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=self._config.min_age_minutes
        )
        stamped = []
        paginator = self._s3.get_paginator("list_objects_v2")
        # Firehose keys are date-prefixed, so listing order is roughly
        # chronological; capping the listing keeps a huge backlog from
        # eating the runtime before any replaying happens.
        pages = paginator.paginate(
            Bucket=self._config.bucket,
            Prefix=self._config.failed_prefix,
            PaginationConfig={"MaxItems": max(limit * 20, 1000)},
        )
        for page in pages:
            for obj in page.get("Contents", []):
                if obj["LastModified"] <= cutoff:
                    stamped.append((obj["LastModified"], obj["Key"]))
        stamped.sort()
        return [key for _, key in stamped[:limit]]

    def read_manifest(self, key: str) -> list[tuple[str, bytes]]:
        """Return (error_code, raw_payload) per line of a Firehose error manifest."""
        body = self._s3.get_object(Bucket=self._config.bucket, Key=key)["Body"].read()
        if body[:2] == GZIP_MAGIC:
            body = gzip.decompress(body)
        records = []
        for line in body.splitlines():
            if not line.strip():
                continue
            document = json.loads(line)
            records.append((
                document.get("errorCode", ""),
                base64.b64decode(document["rawData"]),
            ))
        return records

    def move(self, key: str, dest_prefix: str) -> None:
        """Relocate an object under another prefix (copy first, then delete)."""
        self._s3.copy_object(
            Bucket=self._config.bucket,
            CopySource={"Bucket": self._config.bucket, "Key": key},
            Key=f"{dest_prefix}{key}",
        )
        self._s3.delete_object(Bucket=self._config.bucket, Key=key)


class SplunkHecClient:
    """Post already-transformed HEC event payloads directly to Splunk."""

    def __init__(self, endpoint: str, token: str) -> None:
        # Must match Firehose's hec_endpoint_type (Raw): each JSON line is a
        # whole event. The /event endpoint would treat the keys as HEC metadata.
        self._url = f"{endpoint}/services/collector/raw"
        self._http = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=5.0, read=30.0),
            retries=urllib3.Retry(
                total=3,
                backoff_factor=1.0,
                allowed_methods={"POST"},  # POST is not retried by default
                status_forcelist=HEC_RETRY_STATUSES,
                respect_retry_after_header=True,
                raise_on_status=False,  # let _post classify the final status
            ),
        )
        self._headers = {
            "Authorization": f"Splunk {token}",
            # Required when the token has indexer acknowledgment enabled.
            "X-Splunk-Request-Channel": str(uuid.uuid4()),
        }

    def send(self, payloads: list[bytes]) -> None:
        """Send payloads in size-bounded batches; raise on any rejection."""
        batch: list[bytes] = []
        size = 0
        for payload in payloads:
            if batch and size + len(payload) > HEC_BATCH_BYTES:
                self._post(b"".join(batch))
                batch, size = [], 0
            batch.append(payload)
            size += len(payload)
        if batch:
            self._post(b"".join(batch))

    def _post(self, body: bytes) -> None:
        response = self._http.request(
            "POST", self._url, body=body, headers=self._headers
        )
        summary = f"HEC returned {response.status}: {response.data[:200]!r}"
        if response.status == 200:
            try:
                code = json.loads(response.data).get("code", 0)
            except (ValueError, AttributeError):
                code = 0
            if code == 0:
                return
            # 200 with a non-zero code is HEC saying the data itself is bad.
            raise PermanentRejection(summary)
        # 4xx (other than the retryable ones) means Splunk rejected the data
        # or the token itself; sending the same bytes again cannot succeed.
        if 400 <= response.status < 500 and response.status not in HEC_RETRY_STATUSES:
            raise PermanentRejection(summary)
        raise RuntimeError(summary)


class FirehoseReingester:
    """Re-ingest raw CloudWatch envelopes into the delivery stream."""

    def __init__(self, stream: str, firehose_client) -> None:
        self._stream = stream
        self._firehose = firehose_client

    def send(self, payloads: list[bytes]) -> None:
        """Put payloads back into Firehose in count- and size-bounded batches."""
        batch: list[bytes] = []
        size = 0
        for payload in payloads:
            over_count = len(batch) == FIREHOSE_BATCH_RECORDS
            over_size = size + len(payload) > FIREHOSE_BATCH_BYTES
            if batch and (over_count or over_size):
                self._put_batch(batch)
                batch, size = [], 0
            batch.append(payload)
            size += len(payload)
        if batch:
            self._put_batch(batch)

    def _put_batch(self, batch: list[bytes]) -> None:
        response = self._firehose.put_record_batch(
            DeliveryStreamName=self._stream,
            Records=[{"Data": payload} for payload in batch],
        )
        failed = response.get("FailedPutCount", 0)
        if failed:
            raise RuntimeError(
                f"{failed}/{len(batch)} records rejected by {self._stream}"
            )


class ObjectReplayer:
    """Replay one failed object, routing each record by its errorCode."""

    def __init__(self, config: ReplayConfig, store: FailedObjectStore,
                 hec: SplunkHecClient, reingester: FirehoseReingester) -> None:
        self._config = config
        self._store = store
        self._hec = hec
        self._reingester = reingester

    def replay(self, key: str) -> str:
        """Replay one object; return 'replayed' or 'quarantined'."""
        to_splunk: list[bytes] = []
        to_firehose: list[bytes] = []
        quarantine = False

        for code, payload in self._store.read_manifest(key):
            if code in self._config.permanent_error_codes:
                # The transform rejected this record; re-ingesting it would
                # fail identically. Keep the object for a human.
                quarantine = True
            elif code.startswith(SPLUNK_ERROR_PREFIX):
                to_splunk.append(payload)
            else:
                to_firehose.append(payload)

        if to_splunk:
            self._hec.send(to_splunk)
        if to_firehose:
            self._reingester.send(to_firehose)

        destination = (
            self._config.quarantine_prefix if quarantine
            else self._config.replayed_prefix
        )
        self._store.move(key, destination)
        return "quarantined" if quarantine else "replayed"


CONFIG = ReplayConfig.from_env()
SESSION = boto3.Session()
# Built once per container: constructing clients rebuilds botocore metadata
# and the TLS pool, which is wasted work on every scheduled run.
S3_CLIENT = SESSION.client("s3")
FIREHOSE_CLIENT = SESSION.client("firehose")
SECRETS_CLIENT = SESSION.client("secretsmanager")


def _hec_token() -> str:
    raw = SECRETS_CLIENT.get_secret_value(
        SecretId=CONFIG.hec_secret_arn
    )["SecretString"]
    try:
        parsed = json.loads(raw)
    except ValueError:
        return raw.strip()
    if isinstance(parsed, dict):
        return str(parsed.get("token") or parsed.get("hec_token") or raw).strip()
    return raw.strip()


def handler(event: dict, context: object) -> dict:
    """Scheduled entry point: replay aged failed objects, oldest first."""
    store = FailedObjectStore(CONFIG, S3_CLIENT)
    replayer = ObjectReplayer(
        CONFIG,
        store,
        SplunkHecClient(CONFIG.hec_endpoint, _hec_token()),
        FirehoseReingester(CONFIG.delivery_stream, FIREHOSE_CLIENT),
    )

    counts = {"replayed": 0, "quarantined": 0, "failed": 0}
    remaining_objects = CONFIG.max_objects
    for key in store.eligible_keys(remaining_objects):
        if context.get_remaining_time_in_millis() < TIME_GUARD_MS:
            logger.warning("approaching timeout, stopping before %s", key)
            break
        try:
            counts[replayer.replay(key)] += 1
        except PermanentRejection as exc:
            store.move(key, CONFIG.quarantine_prefix)
            counts["quarantined"] += 1
            logger.error("quarantined %s: %s", key, exc)
        except (ClientError, RuntimeError, urllib3.exceptions.HTTPError,
                ValueError, KeyError, OSError) as exc:
            counts["failed"] += 1
            logger.error("replay failed for %s (will retry): %s", key, exc)
        remaining_objects -= 1

    logger.info("replayed=%d quarantined=%d failed=%d remaining_objects=%d",
                counts["replayed"], counts["quarantined"], counts["failed"],
                remaining_objects)
    return counts
