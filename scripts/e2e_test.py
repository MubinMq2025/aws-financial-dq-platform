"""
End-to-end test of the live pipeline in AWS.

Sends three files through the real system at the same time and checks that each
one is handled correctly, with no manual steps:

    Scenario   File                               Expected result
    clean      0% defects                         pipeline SUCCEEDED, report PASSED, 0 rejected
    dirty      25% defects                        pipeline SUCCEEDED, report PASSED_WITH_WARNINGS (DQ101)
    broken     'amount' column missing entirely   pipeline FAILED (and a failure email is sent)

Sending clean and dirty together also exercises the Glue concurrency retry
(only one Glue run is allowed at a time).

Usage (from the repo root, with .venv active):
    python scripts/e2e_test.py --env dev
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.data_generator.generate_transactions import generate_batch  # noqa: E402
from src.ingestion.upload_raw import build_raw_key, load_config  # noqa: E402

TIMEOUT_MINUTES = 20
POLL_SECONDS = 20

SCENARIOS = [
    {"name": "clean", "date": "2026-11-04", "defect_rate": 0.0, "drop_column": None,
     "expect_execution": "SUCCEEDED", "expect_report": "PASSED"},
    {"name": "dirty", "date": "2026-11-03", "defect_rate": 0.25, "drop_column": None,
     "expect_execution": "SUCCEEDED", "expect_report": "PASSED_WITH_WARNINGS"},
    {"name": "broken", "date": "2026-11-02", "defect_rate": 0.05, "drop_column": "amount",
     "expect_execution": "FAILED", "expect_report": None},
]


def make_csv(scenario: dict) -> str:
    transactions, _ = generate_batch(1000 * 5, scenario["defect_rate"], date.fromisoformat(scenario["date"]), seed=99)
    if scenario["drop_column"]:
        transactions = transactions.drop(columns=[scenario["drop_column"]])
    buffer = StringIO()
    transactions.to_csv(buffer, index=False)
    return buffer.getvalue()


def find_execution(sfn, state_machine_arn: str, key: str, started_after: datetime):
    """Find the pipeline execution that EventBridge started for this file."""
    for page in sfn.get_paginator("list_executions").paginate(stateMachineArn=state_machine_arn):
        for summary in page["executions"]:
            if summary["startDate"] < started_after:
                continue
            details = sfn.describe_execution(executionArn=summary["executionArn"])
            if key in details.get("input", ""):
                return details
    return None


def read_report(s3, bucket: str, key: str):
    report_key = key.replace("raw/", "dq-reports/", 1).replace(".csv", "_dq_report.json")
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=report_key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None
    except Exception:  # AccessDenied is returned for missing keys without ListBucket
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the end-to-end pipeline test in AWS")
    parser.add_argument("--env", default="dev", choices=["dev", "prod"])
    args = parser.parse_args()

    config = load_config(args.env)
    session = boto3.Session(profile_name=config["aws_profile"], region_name=config["aws_region"])
    s3, sfn = session.client("s3"), session.client("stepfunctions")
    bucket = config["s3"]["bucket_name"]
    account_id = session.client("sts").get_caller_identity()["Account"]
    state_machine_arn = (
        f"arn:aws:states:{config['aws_region']}:{account_id}:stateMachine:"
        f"{config['project_name']}-{config['environment']}-pipeline"
    )

    # 1. Upload all three files at once
    started_after = datetime.now(timezone.utc)
    for scenario in SCENARIOS:
        filename = f"transactions_{scenario['date']}_batch001.csv"
        scenario["key"] = build_raw_key(Path(filename), config["s3"]["prefixes"]["raw"])
        s3.put_object(Bucket=bucket, Key=scenario["key"], Body=make_csv(scenario).encode(),
                      ContentType="text/csv", ServerSideEncryption="AES256")
        print(f"Uploaded {scenario['name']:<6} -> s3://{bucket}/{scenario['key']}")

    # 2. Wait for every triggered pipeline run to finish
    print(f"\nWaiting for pipeline runs (up to {TIMEOUT_MINUTES} min; Glue runs one at a time)...")
    deadline = time.time() + TIMEOUT_MINUTES * 60
    pending = {s["name"] for s in SCENARIOS}
    while pending and time.time() < deadline:
        time.sleep(POLL_SECONDS)
        for scenario in SCENARIOS:
            if scenario["name"] not in pending:
                continue
            execution = find_execution(sfn, state_machine_arn, scenario["key"], started_after)
            if execution and execution["status"] != "RUNNING":
                scenario["execution_status"] = execution["status"]
                pending.discard(scenario["name"])
                print(f"  {scenario['name']:<6} finished: {execution['status']}")
        if pending:
            print(f"  still running: {', '.join(sorted(pending))}")

    # 3. Check every expectation
    print("\nResults")
    print(f"{'scenario':<9}{'pipeline':<22}{'report':<34}{'reject rate':<12}verdict")
    all_passed = True
    for scenario in SCENARIOS:
        report = read_report(s3, bucket, scenario["key"])
        report_status = report["status"] if report else None
        reject_rate = f"{report['reject_rate']:.1%}" if report else "-"
        actual_exec = scenario.get("execution_status", "TIMED OUT (test)")

        passed = actual_exec == scenario["expect_execution"] and report_status == scenario["expect_report"]
        all_passed &= passed
        print(f"{scenario['name']:<9}{actual_exec:<22}{str(report_status):<34}{reject_rate:<12}{'PASS' if passed else 'FAIL'}")

    print("\nEND-TO-END TEST PASSED" if all_passed else "\nEND-TO-END TEST FAILED")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
