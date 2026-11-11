"""
AWS Lambda entry point for the data quality validator.

Flow for each raw file:
    1. Download the CSV from S3 (raw/ zone)
    2. Run the shared validation engine (src/dq_platform/validator.py)
    3. Upload valid rows, rejected rows and the JSON report to their S3 zones

Accepted event shapes:
    Manual test:        {"key": "raw/year=2026/month=11/day=08/transactions_...csv"}
                        (optional "bucket"; defaults to the DATA_BUCKET env variable)
    S3 notification:    {"Records": [{"s3": {"bucket": {"name": ...}, "object": {"key": ...}}}]}
    EventBridge (S3):   {"detail": {"bucket": {"name": ...}, "object": {"key": ...}}}

Re-running on the same file overwrites the same output keys, so retries are safe (idempotent).
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
import urllib.parse

import boto3
import pandas as pd

from src.dq_platform.outputs import build_report, output_keys
from src.dq_platform.rules import load_rules
from src.dq_platform.validator import validate

logger = logging.getLogger()
logger.setLevel(logging.INFO)

RAW_PREFIX = "raw/"
ZONE_PREFIXES = {"validated": "validated/", "rejected": "rejected/", "dq_reports": "dq-reports/"}
METRICS_NAMESPACE = "FinDQ"
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

# Created once per Lambda container and reused across warm invocations (faster)
s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-southeast-2"))
RULES = load_rules()


def extract_targets(event: dict) -> list[tuple[str, str]]:
    """Return (bucket, key) pairs from any supported event shape."""
    default_bucket = os.environ.get("DATA_BUCKET")

    if "Records" in event:  # S3 notification
        return [
            (r["s3"]["bucket"]["name"], urllib.parse.unquote_plus(r["s3"]["object"]["key"]))
            for r in event["Records"]
        ]
    if "detail" in event:  # EventBridge "Object Created"
        detail = event["detail"]
        return [(detail["bucket"]["name"], detail["object"]["key"])]
    if "key" in event:  # manual test
        bucket = event.get("bucket", default_bucket)
        if not bucket:
            raise ValueError("No bucket in event and DATA_BUCKET is not set")
        return [(bucket, event["key"])]

    raise ValueError(f"Unrecognised event shape: {json.dumps(event)[:500]}")


def put_csv(bucket: str, key: str, df: pd.DataFrame) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=df.to_csv(index=False).encode("utf-8"),
        ContentType="text/csv",
        ServerSideEncryption="AES256",
    )


def publish_metrics(source_key: str, report: dict) -> None:
    """
    Emit data quality metrics using CloudWatch Embedded Metric Format (EMF).

    Printing this specially shaped JSON line to the logs is enough: CloudWatch
    turns it into real metrics automatically. No extra API calls or permissions.
    """
    print(json.dumps({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": METRICS_NAMESPACE,
                "Dimensions": [["Environment"]],
                "Metrics": [
                    {"Name": "TotalRows", "Unit": "Count"},
                    {"Name": "RejectedRows", "Unit": "Count"},
                    {"Name": "RejectRatePercent", "Unit": "Percent"},
                    {"Name": "Warnings", "Unit": "Count"},
                ],
            }],
        },
        # Dimension value
        "Environment": ENVIRONMENT,
        # Metric values
        "TotalRows": report["total_rows"],
        "RejectedRows": report["rejected_rows"],
        "RejectRatePercent": round(report["reject_rate"] * 100, 2),
        "Warnings": len(report["warnings"]),
        # Extra searchable context (not metrics)
        "event": "file_validated",
        "source_key": source_key,
        "status": report["status"],
        "warning_rules": [w["rule_id"] for w in report["warnings"]],
    }))


def process_file(bucket: str, key: str) -> dict:
    """Validate one raw file and write its three outputs. Returns the report."""
    logger.info("Processing s3://%s/%s", bucket, key)

    obj = s3.get_object(Bucket=bucket, Key=key)
    df = pd.read_csv(io.BytesIO(obj["Body"].read()), dtype=str, keep_default_na=False)

    result = validate(df, RULES)
    report = build_report(result, RULES, key)
    keys = output_keys(key, ZONE_PREFIXES)

    put_csv(bucket, keys["validated"], result.valid)
    put_csv(bucket, keys["rejected"], result.rejected)
    s3.put_object(
        Bucket=bucket,
        Key=keys["dq_report"],
        # One line per report: Athena's JSON reader needs one JSON object per line
        Body=json.dumps(report).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )

    publish_metrics(key, report)
    return report


def lambda_handler(event, context):
    processed, skipped = [], []

    for bucket, key in extract_targets(event):
        if not (key.startswith(RAW_PREFIX) and key.endswith(".csv")):
            logger.info("Skipping non-raw or non-CSV object: %s", key)
            skipped.append(key)
            continue
        report = process_file(bucket, key)
        processed.append({k: report[k] for k in ("source_file", "status", "total_rows", "valid_rows", "rejected_rows")})

    return {"processed": processed, "skipped": skipped}
