"""
Data quality validation engine.

Takes a DataFrame of raw transactions plus the rules contract and returns:
  - valid rows
  - rejected rows, each with the IDs of the rules it failed (rejection_reasons)
  - metrics and warnings for the data quality report

The engine does no file or S3 I/O. That keeps it easy to test and lets the same
code run on a laptop, in AWS Lambda, or anywhere else.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    valid: pd.DataFrame
    rejected: pd.DataFrame
    rule_failures: dict[str, int]
    warnings: list[dict] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return len(self.valid) + len(self.rejected)

    @property
    def reject_rate(self) -> float:
        return len(self.rejected) / self.total_rows if self.total_rows else 0.0


# ---------------------------------------------------------------------------
# Row checks. Each returns a boolean Series: True = row FAILS the rule.
# All raw values are strings, so type problems can be detected, not crashed on.
# ---------------------------------------------------------------------------

def _is_blank(series: pd.Series) -> pd.Series:
    return series.isna() | (series.astype(str).str.strip() == "")


def check_not_null(df: pd.DataFrame, rule: dict, **_) -> pd.Series:
    failed = pd.Series(False, index=df.index)
    for col in rule["columns"]:
        failed |= _is_blank(df[col])
    return failed


def check_unique(df: pd.DataFrame, rule: dict, **_) -> pd.Series:
    col = df[rule["column"]]
    # Keep the first occurrence; later repeats fail. Blank IDs are handled by not_null.
    return col.duplicated(keep="first") & ~_is_blank(col)


def check_numeric(df: pd.DataFrame, rule: dict, **_) -> pd.Series:
    col = df[rule["column"]]
    return pd.to_numeric(col, errors="coerce").isna() & ~_is_blank(col)


def check_range(df: pd.DataFrame, rule: dict, **_) -> pd.Series:
    values = pd.to_numeric(df[rule["column"]], errors="coerce")
    failed = pd.Series(False, index=df.index)
    if "min_exclusive" in rule:
        failed |= values <= rule["min_exclusive"]
    if "max_inclusive" in rule:
        failed |= values > rule["max_inclusive"]
    return failed & values.notna()  # non-numeric values are handled by check_numeric


def _parse_timestamps(df: pd.DataFrame, rule: dict, fmt: str) -> pd.Series:
    return pd.to_datetime(df[rule["column"]], format=fmt, errors="coerce", utc=True)


def check_timestamp_format(df: pd.DataFrame, rule: dict, **_) -> pd.Series:
    col = df[rule["column"]]
    return _parse_timestamps(df, rule, rule["format"]).isna() & ~_is_blank(col)


def check_not_future(df: pd.DataFrame, rule: dict, now: datetime, ts_format: str, **_) -> pd.Series:
    parsed = _parse_timestamps(df, rule, ts_format)
    limit = pd.Timestamp(now + timedelta(minutes=rule.get("tolerance_minutes", 0)))
    return (parsed > limit).fillna(False)


def check_allowed_values(df: pd.DataFrame, rule: dict, **_) -> pd.Series:
    col = df[rule["column"]]
    return ~col.isin(rule["values"]) & ~_is_blank(col)


ROW_CHECKS = {
    "not_null": check_not_null,
    "unique": check_unique,
    "numeric": check_numeric,
    "range": check_range,
    "timestamp_format": check_timestamp_format,
    "not_future": check_not_future,
    "allowed_values": check_allowed_values,
}


def _timestamp_format(rules: dict) -> str:
    """Find the timestamp format declared in the contract (used by not_future)."""
    for rule in rules["row_rules"]:
        if rule["check"] == "timestamp_format":
            return rule["format"]
    return "%Y-%m-%dT%H:%M:%SZ"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def validate(df: pd.DataFrame, rules: dict, now: datetime | None = None) -> ValidationResult:
    """Apply every row rule, split valid/rejected rows, then apply file rules."""
    now = now or datetime.now(timezone.utc)
    ts_format = _timestamp_format(rules)

    missing = [c for c, spec in rules["columns"].items() if spec.get("required") and c not in df.columns]
    if missing:
        raise ValueError(f"Input is missing required columns: {missing}")

    df = df.reset_index(drop=True)
    reasons = pd.Series([[] for _ in range(len(df))], index=df.index, dtype=object)
    rule_failures: dict[str, int] = {}

    for rule in rules["row_rules"]:
        failed = ROW_CHECKS[rule["check"]](df, rule, now=now, ts_format=ts_format)
        rule_failures[rule["id"]] = int(failed.sum())
        for idx in failed[failed].index:
            reasons.at[idx].append(rule["id"])
        logger.info("%s %-28s failed rows: %s", rule["id"], rule["name"], rule_failures[rule["id"]])

    is_rejected = reasons.apply(bool)
    valid = df[~is_rejected].copy()
    rejected = df[is_rejected].copy()
    rejected["rejection_reasons"] = reasons[is_rejected].apply(";".join)

    result = ValidationResult(valid=valid, rejected=rejected, rule_failures=rule_failures)
    result.warnings = _apply_file_rules(rules.get("file_rules", []), result)
    return result


def _apply_file_rules(file_rules: list[dict], result: ValidationResult) -> list[dict]:
    warnings = []
    for rule in file_rules:
        if rule["check"] == "row_count_between":
            n = result.total_rows
            if not rule["min"] <= n <= rule["max"]:
                warnings.append({"rule_id": rule["id"], "message": f"Row count {n} outside expected range {rule['min']}-{rule['max']}"})
        elif rule["check"] == "reject_rate_below":
            if result.reject_rate >= rule["max_rate"]:
                warnings.append({"rule_id": rule["id"], "message": f"Reject rate {result.reject_rate:.1%} at or above {rule['max_rate']:.0%}"})
    for w in warnings:
        logger.warning("%s: %s", w["rule_id"], w["message"])
    return warnings
