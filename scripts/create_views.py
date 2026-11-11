"""
Create (or update) the Athena views stored in sql/views/.

Views are kept as .sql files in Git ("views as code"), so they are reviewed,
versioned and can be rebuilt in any environment with one command.

Usage (from the repo root, with .venv active and AWS credentials exported):
    python scripts/create_views.py --env dev
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import boto3
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEWS_DIR = REPO_ROOT / "sql" / "views"


def load_config(env: str) -> dict:
    with (REPO_ROOT / "config" / f"{env}.yaml").open() as f:
        return yaml.safe_load(f)


def athena_client(config: dict):
    """Use exported credentials if present, otherwise the named CLI profile."""
    region = config["aws_region"]
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        return boto3.Session(region_name=region).client("athena")
    return boto3.Session(profile_name=config["aws_profile"], region_name=region).client("athena")


def run_query(athena, sql: str, database: str, workgroup: str) -> tuple[str, str]:
    """Run one statement and wait for it to finish. Returns (state, reason)."""
    execution_id = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=workgroup,
    )["QueryExecutionId"]

    while True:
        status = athena.get_query_execution(QueryExecutionId=execution_id)["QueryExecution"]["Status"]
        if status["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return status["State"], status.get("StateChangeReason", "")
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create Athena views from sql/views/*.sql")
    parser.add_argument("--env", default="dev", choices=["dev", "prod"])
    args = parser.parse_args()

    config = load_config(args.env)
    # These names match what Terraform creates (see terraform/athena.tf and glue_catalog.tf)
    workgroup = f"{config['project_name']}-{config['environment']}"
    database = f"{config['project_name'].replace('-', '_')}_{config['environment']}"

    athena = athena_client(config)
    failures = 0

    for sql_file in sorted(VIEWS_DIR.glob("*.sql")):
        state, reason = run_query(athena, sql_file.read_text(), database, workgroup)
        print(f"{state:<9} {sql_file.name} {reason}")
        failures += state != "SUCCEEDED"

    if failures:
        sys.exit(f"{failures} view(s) failed")
    print(f"All views created in {database} (workgroup {workgroup})")


if __name__ == "__main__":
    main()
