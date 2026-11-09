"""
Load and lightly validate the data quality contract (config/dq_rules.yaml).

Keeping rule loading separate from rule checking means the same engine can run
locally, in AWS Lambda, or in tests, with the rules coming from any source.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULES_PATH = REPO_ROOT / "config" / "dq_rules.yaml"

SUPPORTED_ROW_CHECKS = {"not_null", "unique", "numeric", "range", "timestamp_format", "not_future", "allowed_values"}
SUPPORTED_FILE_CHECKS = {"row_count_between", "reject_rate_below"}


def load_rules(path: Path | str = DEFAULT_RULES_PATH) -> dict:
    """Read the YAML contract and fail fast if it contains unknown checks."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Rules file not found: {path}")

    with path.open() as f:
        rules = yaml.safe_load(f)

    for rule in rules.get("row_rules", []):
        if rule["check"] not in SUPPORTED_ROW_CHECKS:
            raise ValueError(f"Rule {rule['id']}: unsupported row check '{rule['check']}'")
    for rule in rules.get("file_rules", []):
        if rule["check"] not in SUPPORTED_FILE_CHECKS:
            raise ValueError(f"Rule {rule['id']}: unsupported file check '{rule['check']}'")

    return rules
