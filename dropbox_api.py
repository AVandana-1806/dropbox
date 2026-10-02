"""DocuEdge Linking Runner

Load files, start linking, or collect status into a CSV in S3.

Usage:
    API_SECRET_ID=docuedge/api python linking.py --action LOAD_LINKING \
        --dns api.example.com --bucket my-bucket --file lists/files.txt
"""
import argparse
import csv
import io
import json
import logging
import os
import sys
import time
from dataclasses import dataclass

import boto3
import httpx

LOGGER = logging.getLogger("linking")

TRACKER_KEY = "docuedge-migration/processed_files.txt"
BATCH_SIZE = 10
BATCH_WAIT = 2
STATUS_DELAY = 2
START_TRIGGERS = 3
START_API_WAIT = 30
THREADS = 5


def _configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    for name in ("botocore", "boto3", "urllib3", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


_configure_logging()


@dataclass(frozen=True)
class Config:
    """Hold environment-driven configuration."""

    secret_id: str
    region: str
    timeout: float

    @classmethod
    def from_env(cls) -> "Config":
        """Build config from environment variables."""
        return cls(
            secret_id=os.environ["API_SECRET_ID"],
            region=os.environ.get("AWS_REGION", "us-west-2"),
            timeout=float(os.environ.get("HTTP_TIMEOUT", "60")),
        )


CONFIG = Config.from_env()
SESSION = boto3.Session(region_name=CONFIG.region)
S3 = SESSION.client("s3")


def _call(client, method, path, payload):
    try:
        res = client.request(method, path, json=payload)
        res.raise_for_status()
        LOGGER.info("%s %s -> %s %s", method, path, res.status_code, res.text)
        return res
    except httpx.HTTPStatusError as exc:
        LOGGER.error("%s %s -> %s %s", method, path, exc.response.status_code, exc.response.text)
    except httpx.RequestError as exc:
        LOGGER.error("%s %s failed: %s", method, path, exc)
    return None


def _read_keys(bucket, key):
    return S3.get_object(Bucket=bucket, Key=key)["Body"].read().decode().split()


def _read_processed(bucket):
    try:
        return set(_read_keys(bucket, TRACKER_KEY))
    except S3.exceptions.NoSuchKey:
        return set()


def load_linking(client: httpx.Client, bucket: str, file_key: str) -> int:
    """Send unprocessed files to the load API in chunks of BATCH_SIZE and return the failure count."""
    processed = _read_processed(bucket)
    all_files = _read_keys(bucket, file_key)
    remaining = [key for key in all_files if key not in processed]
    chunks = [remaining[i:i + BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
    LOGGER.info(
        "Files: %d total, %d already processed, %d to load in %d chunks",
        len(all_files), len(processed), len(remaining), len(chunks),
    )

    failures = 0
    for i, chunk in enumerate(chunks, 1):
        LOGGER.info("[%d/%d] Loading %d files: %s", i, len(chunks), len(chunk), chunk)
        if _call(client, "POST", "/load", {"Key": chunk}):
            processed.update(chunk)
            S3.put_object(Bucket=bucket, Key=TRACKER_KEY, Body="\n".join(sorted(processed)).encode())
            LOGGER.info("Tracker saved (%d processed)", len(processed))
        else:
            failures += 1
        if i < len(chunks):
            time.sleep(BATCH_WAIT)

    LOGGER.info("LOAD_LINKING done: %d chunks ok, %d failed", len(chunks) - failures, failures)
    return failures


def start_linking(client: httpx.Client) -> int:
    """Trigger linking START_TRIGGERS times and return the failure count."""
    for i in range(1, START_TRIGGERS + 1):
        LOGGER.info("[%d/%d] Triggering start", i, START_TRIGGERS)
        if not _call(client, "POST", "/start", {"threads": THREADS}):
            return 1
        if i < START_TRIGGERS:
            LOGGER.info("Waiting %ds", START_API_WAIT)
            time.sleep(START_API_WAIT)
    LOGGER.info("START_LINKING done")
    return 0


def status_check(client: httpx.Client, bucket: str, file_key: str) -> int:
    """Write the status of every file in the input list to a CSV in S3 and return the failure count."""
    keys = _read_keys(bucket, file_key)
    fields = ("total_documents", "success_count", "failure_count", "processing_count")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["file_key", *fields, "status"])

    failures = 0
    for i, key in enumerate(keys, 1):
        LOGGER.info("[%d/%d] Checking %s", i, len(keys), key)
        res = _call(client, "GET", "/status", {"Key": key})
        try:
            data = res.json() if res else None
        except json.JSONDecodeError:
            LOGGER.error("Non-JSON status for %s", key)
            data = None

        if data is None:
            failures += 1
            writer.writerow([key, "", "", "", "", "ERROR"])
        else:
            writer.writerow([key, *(data.get(f, 0) for f in fields), "OK"])
        if i < len(keys):
            time.sleep(STATUS_DELAY)

    csv_key = f"docuedge-migration/status_update_{int(time.time())}.csv"
    S3.put_object(Bucket=bucket, Key=csv_key, Body=buf.getvalue().encode(), ContentType="text/csv")
    LOGGER.info("STATUS_CHECK done: %d ok, %d failed -> s3://%s/%s", len(keys) - failures, failures, bucket, csv_key)
    return failures


def main() -> int:
    """Run the requested action and return an exit code."""
    parser = argparse.ArgumentParser(description="DocuEdge linking runner")
    parser.add_argument("--action", required=True, choices=["LOAD_LINKING", "START_LINKING", "STATUS_CHECK"])
    parser.add_argument("--dns", required=True, help="API DNS hostname")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--file", required=True, help="S3 key of TXT file listing files")
    args = parser.parse_args()

    LOGGER.info("Action %s against https://%s", args.action, args.dns)
    secret = json.loads(SESSION.client("secretsmanager").get_secret_value(SecretId=CONFIG.secret_id)["SecretString"])
    auth = httpx.BasicAuth(secret["username"], secret["password"])

    started = time.monotonic()
    with httpx.Client(base_url=f"https://{args.dns}/bulkupdate", auth=auth, timeout=CONFIG.timeout) as client:
        if args.action == "LOAD_LINKING":
            failures = load_linking(client, args.bucket, args.file)
        elif args.action == "START_LINKING":
            failures = start_linking(client)
        else:
            failures = status_check(client, args.bucket, args.file)

    LOGGER.info("Finished %s in %.1fs (exit %d)", args.action, time.monotonic() - started, 1 if failures else 0)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
