"""
Upload generated transaction files to the S3 raw zone using date-partitioned keys.

Key layout (Hive-style partitions, understood by Glue and Athena):
    raw/year=YYYY/month=MM/day=DD/<filename>.csv

The business date is read from the filename, e.g.
    transactions_2026-11-08_batch001.csv  ->  raw/year=2026/month=11/day=08/transactions_2026-11-08_batch001.csv

Safe to re-run (idempotent): files already in S3 are skipped unless --overwrite is given.
Defect label files (ground truth for testing) are never uploaded.

Usage (from the repo root, with .venv active):
    python -m src.ingestion.upload_raw --env dev --dry-run      # show what would happen
    python -m src.ingestion.upload_raw --env dev                # upload all new files
    python -m src.ingestion.upload_raw --env dev --file data/sample/transactions_2026-11-08_batch001.csv
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("upload_raw")

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
DEFAULT_SOURCE_DIR = REPO_ROOT / "data" / "sample"

# transactions_2026-11-08_batch001.csv
FILENAME_PATTERN = re.compile(r"^transactions_(\d{4})-(\d{2})-(\d{2})_batch\d{3}\.csv$")


def load_config(env: str) -> dict:
    """Load environment settings from config/<env>.yaml."""
    config_path = CONFIG_DIR / f"{env}.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open() as f:
        return yaml.safe_load(f)


def build_s3_client(config: dict):
    """
    Create an S3 client.

    If temporary credentials are already in environment variables (exported with
    `aws configure export-credentials`), use them. Otherwise fall back to the
    named AWS CLI profile from the config.
    """
    region = config["aws_region"]
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        session = boto3.Session(region_name=region)
    else:
        session = boto3.Session(profile_name=config["aws_profile"], region_name=region)
    return session.client("s3")


def build_raw_key(file_path: Path, raw_prefix: str) -> str:
    """Turn a local filename into a date-partitioned S3 key."""
    match = FILENAME_PATTERN.match(file_path.name)
    if not match:
        raise ValueError(
            f"Unexpected filename '{file_path.name}'. Expected transactions_YYYY-MM-DD_batchNNN.csv"
        )
    year, month, day = match.groups()
    return f"{raw_prefix}year={year}/month={month}/day={day}/{file_path.name}"


def object_exists(s3, bucket: str, key: str) -> bool:
    """Return True if the key is already in the bucket."""
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as err:
        if err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def find_files(source_dir: Path) -> list[Path]:
    """Find transaction files to upload (defect label files are excluded by the pattern)."""
    return sorted(p for p in source_dir.glob("*.csv") if FILENAME_PATTERN.match(p.name))


def upload_files(files: list[Path], config: dict, overwrite: bool, dry_run: bool) -> dict:
    """Upload each file to its partitioned key. Returns a summary count."""
    bucket = config["s3"]["bucket_name"]
    raw_prefix = config["s3"]["prefixes"]["raw"]
    summary = {"uploaded": 0, "skipped": 0, "failed": 0}

    s3 = None if dry_run else build_s3_client(config)

    for file_path in files:
        key = build_raw_key(file_path, raw_prefix)
        target = f"s3://{bucket}/{key}"

        if dry_run:
            logger.info("[DRY RUN] %s -> %s", file_path.name, target)
            continue

        try:
            if not overwrite and object_exists(s3, bucket, key):
                logger.info("SKIP (already exists) %s", target)
                summary["skipped"] += 1
                continue

            s3.upload_file(
                str(file_path),
                bucket,
                key,
                ExtraArgs={"ContentType": "text/csv", "ServerSideEncryption": "AES256"},
            )
            logger.info("UPLOADED %s -> %s", file_path.name, target)
            summary["uploaded"] += 1
        except ClientError as err:
            logger.error("FAILED %s: %s", file_path.name, err)
            summary["failed"] += 1

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload transaction files to the S3 raw zone.")
    parser.add_argument("--env", default="dev", choices=["dev", "prod"], help="Config environment")
    parser.add_argument("--file", type=Path, help="Upload a single file instead of the whole folder")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR, help="Folder of files to upload")
    parser.add_argument("--overwrite", action="store_true", help="Replace files that already exist in S3")
    parser.add_argument("--dry-run", action="store_true", help="Show target keys without uploading")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.env)

    files = [args.file] if args.file else find_files(args.source_dir)
    if not files:
        logger.warning("No transaction files found in %s", args.source_dir)
        return

    logger.info("Found %s file(s) to process (env=%s)", len(files), args.env)
    summary = upload_files(files, config, overwrite=args.overwrite, dry_run=args.dry_run)

    if not args.dry_run:
        logger.info("Done: %s uploaded, %s skipped, %s failed", summary["uploaded"], summary["skipped"], summary["failed"])
        if summary["failed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
