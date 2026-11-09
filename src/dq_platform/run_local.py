"""
Run the validation engine on a local CSV, write the outputs, and (optionally)
check the result against the generator's defect labels.

Outputs go to data/output/ using the same zone and partition layout as S3:
    data/output/validated/year=.../<file>.csv
    data/output/rejected/year=.../<file>_rejected.csv
    data/output/dq-reports/year=.../<file>_dq_report.json

Usage (from the repo root, with .venv active):
    python -m src.dq_platform.run_local --input data/sample/transactions_2026-11-08_batch001.csv
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from src.dq_platform.outputs import build_report, output_keys
from src.dq_platform.rules import REPO_ROOT, load_rules
from src.dq_platform.validator import validate

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("run_local")

DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "output"
ZONE_PREFIXES = {"validated": "validated/", "rejected": "rejected/", "dq_reports": "dq-reports/"}


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


def write_outputs(result, report: dict, source_name: str, output_dir: Path) -> None:
    """Save valid rows, rejected rows and the report, mirroring the S3 layout."""
    keys = output_keys(source_name, ZONE_PREFIXES)
    paths = {zone: output_dir / key for zone, key in keys.items()}
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    result.valid.to_csv(paths["validated"], index=False)
    result.rejected.to_csv(paths["rejected"], index=False)
    paths["dq_report"].write_text(json.dumps(report, indent=2))

    for zone, path in paths.items():
        logger.info("Wrote %-10s -> %s", zone, path.relative_to(REPO_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a transactions CSV locally.")
    parser.add_argument("--input", type=Path, required=True, help="Path to transactions CSV")
    parser.add_argument("--labels", type=Path, help="Defect labels CSV (auto-detected if omitted)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Where to write outputs")
    args = parser.parse_args()

    rules = load_rules()
    df = read_raw_csv(args.input)
    logger.info("Validating %s (%s rows) with contract v%s", args.input.name, len(df), rules["version"])

    result = validate(df, rules)
    logger.info("Valid: %s | Rejected: %s | Reject rate: %.2f%%", len(result.valid), len(result.rejected), 100 * result.reject_rate)

    report = build_report(result, rules, args.input.name)
    write_outputs(result, report, args.input.name, args.output_dir)
    logger.info("Report status: %s", report["status"])

    labels_path = args.labels or args.input.with_name(args.input.name.replace("transactions_", "defect_labels_"))
    if labels_path.exists():
        compare_with_labels(result, labels_path)
    else:
        logger.info("No labels file found, skipping defect comparison")


if __name__ == "__main__":
    main()
