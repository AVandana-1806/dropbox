"""DocuEdge Migration Runner

Drive the linking (/bulkupdate) and ingestion (/migration) APIs: load files, start the job,
or collect per-file status into a CSV in S3.

Usage:
    API_SECRET_ID=docuedge/api python linking.py --action LOAD_DATA \
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

LOGGER = logging.getLogger("docuedge")

FLOWS = {
    "linking": ("/bulkupdate", "docuedge-migration/processed_files.txt"),
    "ingestion": ("/migration", "docuedge-migration/ingestion_processed_files.txt"),
}
STATUS_FIELDS = ("total_documents", "success_count", "failure_count", "processing_count")
DELETE_START_PATH = "/delete/start"
DELETE_COUNT_PATH = "/delete/count"
DELETE_STATUS_PATH = "/delete/status"
BATCH_SIZE = 10
BATCH_WAIT = 2
STATUS_DELAY = 2
START_TRIGGERS = 3
START_API_WAIT = 30
THREADS = 5


def _configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    for name in ("botocore", "httpx"):
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
        if exc.response.status_code == 401:
            raise
    except httpx.RequestError as exc:
        LOGGER.error("%s %s failed: %s", method, path, exc)
    return None


def _read_keys(bucket, key):
    return S3.get_object(Bucket=bucket, Key=key)["Body"].read().decode().split()


def _read_processed(bucket, tracker_key):
    try:
        return set(_read_keys(bucket, tracker_key))
    except S3.exceptions.NoSuchKey:
        return set()


def load_files(client: httpx.Client, bucket: str, file_key: str, flow: str) -> int:
    """Send unprocessed files to the flow's load API in chunks, recording failed files to S3."""
    prefix, tracker_key = FLOWS[flow]
    failed_key = f"docuedge-migration/{flow}_failed_files_{int(time.time())}.txt"
    processed = _read_processed(bucket, tracker_key)
    all_files = _read_keys(bucket, file_key)
    remaining = [key for key in all_files if key not in processed]
    chunks = [remaining[i:i + BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
    LOGGER.info(
        "[%s] Files: %d total, %d already processed, %d to load in %d chunks",
        flow, len(all_files), len(processed), len(remaining), len(chunks),
    )

    failed = []
    for i, chunk in enumerate(chunks, 1):
        LOGGER.info("[%s] [%d/%d] Loading %d files: %s", flow, i, len(chunks), len(chunk), chunk)
        if _call(client, "POST", f"{prefix}/load", {"Key": chunk}):
            processed.update(chunk)
            S3.put_object(Bucket=bucket, Key=tracker_key, Body="\n".join(sorted(processed)).encode())
            LOGGER.info("[%s] Tracker saved (%d processed)", flow, len(processed))
        else:
            failed.extend(chunk)
            S3.put_object(Bucket=bucket, Key=failed_key, Body="\n".join(failed).encode())
            LOGGER.warning("[%s] Chunk failed, %d files recorded in s3://%s/%s", flow, len(failed), bucket, failed_key)
        if i < len(chunks):
            time.sleep(BATCH_WAIT)

    LOGGER.info("[%s] Load done: %d files loaded, %d failed", flow, len(remaining) - len(failed), len(failed))
    if failed:
        LOGGER.warning("[%s] Failed files list: s3://%s/%s", flow, bucket, failed_key)
    return 0


def trigger(client: httpx.Client, label: str, path: str, payload) -> int:
    """POST to path START_TRIGGERS times with START_API_WAIT between calls and return the failure count."""
    for i in range(1, START_TRIGGERS + 1):
        LOGGER.info("[%s] [%d/%d] Triggering %s", label, i, START_TRIGGERS, path)
        if not _call(client, "POST", path, payload):
            return 1
        if i < START_TRIGGERS:
            LOGGER.info("[%s] Waiting %ds", label, START_API_WAIT)
            time.sleep(START_API_WAIT)
    LOGGER.info("[%s] Trigger done", label)
    return 0


def start_job(client: httpx.Client, flow: str) -> int:
    """Trigger the flow's start API and return the failure count."""
    prefix, _ = FLOWS[flow]
    return trigger(client, flow, f"{prefix}/start", {"threads": THREADS})


def check_status(client: httpx.Client, bucket: str, file_key: str, flow: str) -> int:
    """Write the status of every file in the input list to a CSV in S3 and return the failure count."""
    prefix, _ = FLOWS[flow]
    keys = _read_keys(bucket, file_key)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["file_key", *STATUS_FIELDS, "status"])

    failures = 0
    for i, key in enumerate(keys, 1):
        LOGGER.info("[%s] [%d/%d] Checking %s", flow, i, len(keys), key)
        res = _call(client, "GET", f"{prefix}/status", {"Key": key})
        try:
            data = res.json() if res else None
        except json.JSONDecodeError:
            LOGGER.error("[%s] Non-JSON status for %s", flow, key)
            data = None

        if data is None:
            failures += 1
            writer.writerow([key, "", "", "", "", "ERROR"])
        else:
            writer.writerow([key, *(data.get(f, 0) for f in STATUS_FIELDS), "OK"])
        if i < len(keys):
            time.sleep(STATUS_DELAY)

    csv_key = f"docuedge-migration/{flow}_status_{int(time.time())}.csv"
    S3.put_object(Bucket=bucket, Key=csv_key, Body=buf.getvalue().encode(), ContentType="text/csv")
    LOGGER.info("[%s] Status done: %d ok, %d failed -> s3://%s/%s", flow, len(keys) - failures, failures, bucket, csv_key)
    return failures


def call_once(client: httpx.Client, method: str, path: str) -> int:
    """Make a single no-body call, log the response and return 0 on success."""
    LOGGER.info("[delete] %s %s", method, path)
    return 0 if _call(client, method, path, None) else 1


ACTIONS = {
    "LOAD_LINKING": lambda client, args: load_files(client, args.bucket, args.file, "linking"),
    "START_LINKING": lambda client, args: start_job(client, "linking"),
    "STATUS_CHECK": lambda client, args: check_status(client, args.bucket, args.file, "linking"),
    "LOAD_DATA": lambda client, args: load_files(client, args.bucket, args.file, "ingestion"),
    "START_MIGRATION": lambda client, args: start_job(client, "ingestion"),
    "MIGRATION_STATUS": lambda client, args: check_status(client, args.bucket, args.file, "ingestion"),
    "DELETE_START": lambda client, args: trigger(client, "delete", DELETE_START_PATH, None),
    "DELETE_COUNT": lambda client, args: call_once(client, "GET", DELETE_COUNT_PATH),
    "DELETE_STATUS": lambda client, args: call_once(client, "GET", DELETE_STATUS_PATH),
}
FILE_ACTIONS = {"LOAD_LINKING", "STATUS_CHECK", "LOAD_DATA", "MIGRATION_STATUS"}


def main() -> int:
    """Run the requested action and return an exit code."""
    parser = argparse.ArgumentParser(description="DocuEdge migration runner")
    parser.add_argument("--action", required=True, choices=ACTIONS)
    parser.add_argument("--dns", required=True, help="API DNS hostname")
    parser.add_argument("--bucket", help="S3 bucket name (file-based actions)")
    parser.add_argument("--file", help="S3 key of TXT file listing files (file-based actions)")
    args = parser.parse_args()
    if args.action in FILE_ACTIONS and not (args.bucket and args.file):
        parser.error(f"--bucket and --file are required for {args.action}")

    LOGGER.info("Action %s against https://%s", args.action, args.dns)
    secret = json.loads(SESSION.client("secretsmanager").get_secret_value(SecretId=CONFIG.secret_id)["SecretString"])
    auth = httpx.BasicAuth(secret["username"], secret["password"])

    started = time.monotonic()
    with httpx.Client(base_url=f"https://{args.dns}", auth=auth, timeout=CONFIG.timeout) as client:
        try:
            failures = ACTIONS[args.action](client, args)
        except httpx.HTTPStatusError:
            LOGGER.error("Authentication failed (401), stopping %s", args.action)
            return 1

    LOGGER.info("Finished %s in %.1fs (exit %d)", args.action, time.monotonic() - started, 1 if failures else 0)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
