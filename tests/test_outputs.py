"""Unit tests for output paths and the data quality report."""

import pandas as pd
import pytest

from src.dq_platform.outputs import build_report, output_keys, partition_for
from src.dq_platform.validator import ValidationResult

PREFIXES = {"validated": "validated/", "rejected": "rejected/", "dq_reports": "dq-reports/"}


def test_partition_from_s3_key():
    key = "raw/year=2026/month=11/day=08/transactions_2026-11-08_batch001.csv"
    assert partition_for(key) == "year=2026/month=11/day=08"


def test_partition_from_plain_filename():
    assert partition_for("transactions_2026-11-08_batch001.csv") == "year=2026/month=11/day=08"


def test_partition_from_bad_name_raises_error():
    with pytest.raises(ValueError):
        partition_for("random_file.csv")


def test_output_keys_mirror_raw_layout():
    keys = output_keys("raw/year=2026/month=11/day=08/transactions_2026-11-08_batch001.csv", PREFIXES)
    assert keys == {
        "validated": "validated/year=2026/month=11/day=08/transactions_2026-11-08_batch001.csv",
        "rejected": "rejected/year=2026/month=11/day=08/transactions_2026-11-08_batch001_rejected.csv",
        "dq_report": "dq-reports/year=2026/month=11/day=08/transactions_2026-11-08_batch001_dq_report.json",
    }


def test_report_status_and_counts():
    rules = {"version": 1, "row_rules": [{"id": "DQ001", "name": "required_fields_present"}]}
    result = ValidationResult(
        valid=pd.DataFrame({"a": [1, 2, 3]}),
        rejected=pd.DataFrame({"a": [4]}),
        rule_failures={"DQ001": 1},
        warnings=[{"rule_id": "DQ100", "message": "low volume"}],
    )
    report = build_report(result, rules, "transactions_2026-11-08_batch001.csv")
    assert report["status"] == "PASSED_WITH_WARNINGS"
    assert (report["total_rows"], report["valid_rows"], report["rejected_rows"]) == (4, 3, 1)
    assert report["reject_rate"] == 0.25
