"""
Run the validation engine on a local CSV and (optionally) check it against the
generator's defect labels, to prove every injected defect was caught.

Usage (from the repo root, with .venv active):
    python -m src.dq_platform.run_local --input data/sample/transactions_2026-11-08_batch001.csv
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from src.dq_platform.rules import load_rules
from src.dq_platform.validator import validate

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("run_local")


def read_raw_csv(path: Path) -> pd.DataFrame:
    """Read every column as text so bad values are kept exactly as received."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def compare_with_labels(result, labels_path: Path) -> None:
    """Check that every row the generator broke on purpose was rejected."""
    labels = pd.read_csv(labels_path)
    rejected_rows = set(result.rejected.index)
    labels["caught"] = labels["row_number"].isin(rejected_rows)

    caught, total = int(labels["caught"].sum()), len(labels)
    logger.info("Injected defects caught: %s / %s (%.1f%%)", caught, total, 100 * caught / total if total else 100)

    missed = labels[~labels["caught"]]
    if not missed.empty:
        logger.warning("Missed defects by type:\n%s", missed["defect_type"].value_counts().to_string())

    extra = len(rejected_rows) - caught
    logger.info("Rejected rows not in the label file (should be 0): %s", extra)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a transactions CSV locally.")
    parser.add_argument("--input", type=Path, required=True, help="Path to transactions CSV")
    parser.add_argument("--labels", type=Path, help="Defect labels CSV (auto-detected if omitted)")
    args = parser.parse_args()

    rules = load_rules()
    df = read_raw_csv(args.input)
    logger.info("Validating %s (%s rows) with contract v%s", args.input.name, len(df), rules["version"])

    result = validate(df, rules)
    logger.info("Valid: %s | Rejected: %s | Reject rate: %.2f%%", len(result.valid), len(result.rejected), 100 * result.reject_rate)

    labels_path = args.labels or args.input.with_name(args.input.name.replace("transactions_", "defect_labels_"))
    if labels_path.exists():
        compare_with_labels(result, labels_path)
    else:
        logger.info("No labels file found, skipping defect comparison")


if __name__ == "__main__":
    main()
