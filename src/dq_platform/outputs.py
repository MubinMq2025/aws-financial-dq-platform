"""
Turn a ValidationResult into the three outputs the pipeline stores:

    validated/<partition>/<file>.csv                  -> rows that passed every rule
    rejected/<partition>/<file>_rejected.csv          -> failed rows + rejection_reasons
    dq-reports/<partition>/<file>_dq_report.json      -> metrics for monitoring and trend analysis

The same functions are used locally and in AWS Lambda, so both write identical layouts.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from src.dq_platform.validator import ValidationResult

PARTITION_PATTERN = re.compile(r"year=\d{4}/month=\d{2}/day=\d{2}")
DATE_IN_NAME = re.compile(r"_(\d{4})-(\d{2})-(\d{2})_")


def partition_for(source_name: str) -> str:
    """Get the year=/month=/day= partition from an S3 key or filename."""
    match = PARTITION_PATTERN.search(source_name)
    if match:
        return match.group(0)
    date_match = DATE_IN_NAME.search(source_name)
    if not date_match:
        raise ValueError(f"Cannot work out the business date from '{source_name}'")
    year, month, day = date_match.groups()
    return f"year={year}/month={month}/day={day}"


def output_keys(source_name: str, prefixes: dict) -> dict:
    """Build the output paths for each zone, mirroring the raw partition layout."""
    filename = source_name.replace("\\", "/").split("/")[-1]
    stem = filename.removesuffix(".csv")
    partition = partition_for(source_name)
    return {
        "validated": f"{prefixes['validated']}{partition}/{filename}",
        "rejected": f"{prefixes['rejected']}{partition}/{stem}_rejected.csv",
        "dq_report": f"{prefixes['dq_reports']}{partition}/{stem}_dq_report.json",
    }


def build_report(result: ValidationResult, rules: dict, source_name: str, run_time: datetime | None = None) -> dict:
    """Summarise a validation run as a JSON-friendly dictionary."""
    run_time = run_time or datetime.now(timezone.utc)
    rule_names = {r["id"]: r["name"] for r in rules["row_rules"]}

    if result.warnings:
        status = "PASSED_WITH_WARNINGS"
    else:
        status = "PASSED"

    return {
        "source_file": source_name,
        "partition": partition_for(source_name),
        "contract_version": rules["version"],
        "run_timestamp_utc": run_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "total_rows": result.total_rows,
        "valid_rows": len(result.valid),
        "rejected_rows": len(result.rejected),
        "reject_rate": round(result.reject_rate, 4),
        "rule_failures": [
            {"rule_id": rule_id, "rule_name": rule_names[rule_id], "failed_rows": count}
            for rule_id, count in result.rule_failures.items()
        ],
        "warnings": result.warnings,
    }
