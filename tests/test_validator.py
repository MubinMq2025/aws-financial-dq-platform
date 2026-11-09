"""
Unit tests for the validation engine.

Each test builds a tiny, hand-made table so we know exactly what the right
answer is. No files, no AWS: these run in under a second, anywhere.
"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.dq_platform.rules import load_rules
from src.dq_platform.validator import validate

# Fixed "now" so tests give the same result on any day
NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def make_row(**overrides) -> dict:
    """A valid transaction. Override any field to break it on purpose."""
    row = {
        "transaction_id": "TXN-0001",
        "customer_id": "CUST-000001",
        "merchant_id": "MER-00001",
        "transaction_ts": "2026-11-10T09:30:00Z",
        "amount": "42.50",
        "currency": "AUD",
        "payment_method": "card",
        "payment_source": "pos_terminal",
        "status": "approved",
        "country": "AU",
        "merchant_name": "Test Cafe",
    }
    row.update(overrides)
    return row


def run_one(rules, **overrides):
    """Validate a single row and return its rejection reasons ('' if valid)."""
    result = validate(pd.DataFrame([make_row(**overrides)]), rules, now=NOW)
    return "" if result.rejected.empty else result.rejected.iloc[0]["rejection_reasons"]


# --- Happy path -------------------------------------------------------------

def test_valid_row_passes(rules):
    assert run_one(rules) == ""


# --- One test per rule ------------------------------------------------------

@pytest.mark.parametrize(
    "field, bad_value, expected_rule",
    [
        ("customer_id", "", "DQ001"),               # missing mandatory field
        ("merchant_id", "   ", "DQ001"),            # whitespace counts as missing
        ("amount", "12.5O", "DQ003"),               # letter O instead of zero
        ("amount", "$45.00", "DQ003"),              # currency symbol in number
        ("amount", "-10.00", "DQ004"),              # negative
        ("amount", "0.00", "DQ004"),                # zero
        ("amount", "2000000.00", "DQ004"),          # unrealistically large
        ("transaction_ts", "31/02/2026 25:61", "DQ005"),  # wrong format
        ("transaction_ts", "2026-13-01T00:00:00Z", "DQ005"),  # month 13
        ("transaction_ts", "2027-01-01T00:00:00Z", "DQ006"),  # in the future
        ("currency", "aud", "DQ007"),               # lowercase
        ("currency", "XYZ", "DQ007"),               # not an approved code
        ("payment_method", "cash", "DQ008"),
        ("status", "pending", "DQ009"),
    ],
)
def test_each_rule_rejects_bad_value(rules, field, bad_value, expected_rule):
    assert expected_rule in run_one(rules, **{field: bad_value}).split(";")


def test_amount_on_upper_limit_is_allowed(rules):
    assert run_one(rules, amount="1000000.00") == ""


def test_timestamp_within_clock_drift_tolerance_is_allowed(rules):
    # 3 minutes ahead of "now" is inside the 5 minute tolerance
    assert run_one(rules, transaction_ts="2026-11-10T12:03:00Z") == ""


def test_duplicate_keeps_first_rejects_second(rules):
    df = pd.DataFrame([make_row(), make_row(amount="99.99")])
    result = validate(df, rules, now=NOW)
    assert len(result.valid) == 1 and result.valid.iloc[0]["amount"] == "42.50"
    assert result.rejected.iloc[0]["rejection_reasons"] == "DQ002"


def test_row_breaking_two_rules_lists_both(rules):
    reasons = run_one(rules, customer_id="", currency="aud").split(";")
    assert {"DQ001", "DQ007"} <= set(reasons)


def test_missing_required_column_raises_error(rules):
    df = pd.DataFrame([make_row()]).drop(columns=["amount"])
    with pytest.raises(ValueError, match="missing required columns"):
        validate(df, rules, now=NOW)


# --- File-level rules -------------------------------------------------------

def test_low_volume_raises_warning_not_rejection(rules):
    df = pd.DataFrame([make_row(transaction_id=f"TXN-{i}") for i in range(10)])
    result = validate(df, rules, now=NOW)
    assert len(result.rejected) == 0
    assert [w["rule_id"] for w in result.warnings] == ["DQ100"]


def test_high_reject_rate_raises_warning(rules):
    good = [make_row(transaction_id=f"TXN-G{i}") for i in range(1000)]
    bad = [make_row(transaction_id=f"TXN-B{i}", amount="-1") for i in range(200)]
    result = validate(pd.DataFrame(good + bad), rules, now=NOW)
    assert "DQ101" in [w["rule_id"] for w in result.warnings]
