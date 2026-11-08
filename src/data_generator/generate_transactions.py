"""
Synthetic financial transaction generator.

Creates a CSV of realistic-looking (fake) payment transactions and deliberately
injects data quality defects into a configurable share of rows. A separate
"defect labels" file records exactly which rows were broken and how, so we can
later prove the validation engine catches every defect.

Usage (from the repo root, with .venv active):
    python -m src.data_generator.generate_transactions --env dev
    python -m src.data_generator.generate_transactions --env dev --date 2026-11-08 --batch 2 --seed 7
    python -m src.data_generator.generate_transactions --env dev --records 500   # override volume

Outputs (in data/sample/):
    transactions_<date>_batch<NNN>.csv          -> the data the pipeline ingests
    defect_labels_<date>_batch<NNN>.csv         -> ground truth for testing only
"""

from __future__ import annotations

import argparse
import logging
import random
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml
from faker import Faker

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("data_generator")

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
OUTPUT_DIR = REPO_ROOT / "data" / "sample"

# Reference values for clean records
CURRENCIES = ["AUD", "USD", "EUR", "GBP", "SGD"]
PAYMENT_METHODS = ["card", "bank_transfer", "digital_wallet", "bnpl"]
PAYMENT_SOURCES = ["stripe_gateway", "bank_feed", "pos_terminal", "mobile_app"]
STATUSES = ["approved", "declined", "refunded"]
STATUS_WEIGHTS = [0.90, 0.07, 0.03]
COUNTRIES = ["AU", "NZ", "US", "GB", "SG"]

# Defect types we inject, each with a function that breaks one row
DEFECT_TYPES = [
    "duplicate_transaction_id",
    "missing_customer_id",
    "missing_merchant_id",
    "negative_amount",
    "zero_amount",
    "extreme_amount",
    "non_numeric_amount",
    "unparseable_timestamp",
    "future_timestamp",
    "invalid_currency",
    "lowercase_currency",
]


def load_config(env: str) -> dict:
    """Load environment settings from config/<env>.yaml."""
    config_path = CONFIG_DIR / f"{env}.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open() as f:
        return yaml.safe_load(f)


def make_clean_transaction(fake: Faker, batch_date: date, customers: list[str], merchants: list[str]) -> dict:
    """Build one valid transaction for the given business date."""
    start = datetime(batch_date.year, batch_date.month, batch_date.day, tzinfo=timezone.utc)
    ts = start + timedelta(seconds=random.randint(0, 86_399))
    return {
        "transaction_id": f"TXN-{uuid.uuid4().hex[:16].upper()}",
        "customer_id": random.choice(customers),
        "merchant_id": random.choice(merchants),
        "transaction_ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Log-normal gives many small payments and a few large ones, like real data
        "amount": f"{round(min(random.lognormvariate(3.5, 1.0), 9_999.99), 2):.2f}",
        "currency": random.choice(CURRENCIES),
        "payment_method": random.choice(PAYMENT_METHODS),
        "payment_source": random.choice(PAYMENT_SOURCES),
        "status": random.choices(STATUSES, weights=STATUS_WEIGHTS)[0],
        "country": random.choice(COUNTRIES),
        "merchant_name": fake.company(),
    }


def inject_defect(row: dict, defect: str, existing_ids: list[str]) -> dict:
    """Return a copy of the row broken in the specified way."""
    row = dict(row)
    if defect == "duplicate_transaction_id":
        row["transaction_id"] = random.choice(existing_ids)
    elif defect == "missing_customer_id":
        row["customer_id"] = ""
    elif defect == "missing_merchant_id":
        row["merchant_id"] = ""
    elif defect == "negative_amount":
        row["amount"] = f"-{row['amount']}"
    elif defect == "zero_amount":
        row["amount"] = "0.00"
    elif defect == "extreme_amount":
        row["amount"] = f"{random.uniform(1_000_000, 50_000_000):.2f}"
    elif defect == "non_numeric_amount":
        row["amount"] = random.choice(["12.5O", "N/A", "$45.00", "abc"])
    elif defect == "unparseable_timestamp":
        row["transaction_ts"] = random.choice(["31/02/2026 25:61", "not-a-date", "2026-13-01T00:00:00Z"])
    elif defect == "future_timestamp":
        row["transaction_ts"] = (datetime.now(timezone.utc) + timedelta(days=random.randint(30, 400))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    elif defect == "invalid_currency":
        row["currency"] = random.choice(["XYZ", "A$", "DOLLAR", ""])
    elif defect == "lowercase_currency":
        row["currency"] = row["currency"].lower()
    else:
        raise ValueError(f"Unknown defect type: {defect}")
    return row


def generate_batch(num_records: int, defect_rate: float, batch_date: date, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate transactions plus a ground-truth table of injected defects."""
    random.seed(seed)
    fake = Faker("en_AU")
    Faker.seed(seed)

    customers = [f"CUST-{n:06d}" for n in random.sample(range(1, 999_999), k=max(50, num_records // 10))]
    merchants = [f"MER-{n:05d}" for n in random.sample(range(1, 99_999), k=max(20, num_records // 50))]

    rows: list[dict] = []
    labels: list[dict] = []

    for i in range(num_records):
        row = make_clean_transaction(fake, batch_date, customers, merchants)
        # Only inject a defect once we have earlier IDs available to duplicate
        if i > 0 and random.random() < defect_rate:
            defect = random.choice(DEFECT_TYPES)
            row = inject_defect(row, defect, [r["transaction_id"] for r in rows[-500:]])
            labels.append({"row_number": i, "transaction_id": row["transaction_id"], "defect_type": defect})
        rows.append(row)

    return pd.DataFrame(rows), pd.DataFrame(labels, columns=["row_number", "transaction_id", "defect_type"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate synthetic financial transactions with injected defects.")
    parser.add_argument("--env", default="dev", choices=["dev", "prod"], help="Config environment to use")
    parser.add_argument("--date", default=date.today().isoformat(), help="Business date, YYYY-MM-DD")
    parser.add_argument("--batch", type=int, default=1, help="Batch number for this date")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible output")
    parser.add_argument("--records", type=int, help="Override number of records (e.g. to simulate a volume anomaly)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.env)
    gen_cfg = config["data_generation"]

    num_records = args.records or gen_cfg["num_records"]
    defect_rate = gen_cfg["defect_rate"]
    batch_date = date.fromisoformat(args.date)

    logger.info("Generating %s records for %s (env=%s, defect_rate=%.0f%%)", num_records, batch_date, args.env, defect_rate * 100)
    transactions, defects = generate_batch(num_records, defect_rate, batch_date, args.seed)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"{batch_date.isoformat()}_batch{args.batch:03d}"
    data_path = OUTPUT_DIR / f"transactions_{suffix}.csv"
    labels_path = OUTPUT_DIR / f"defect_labels_{suffix}.csv"

    transactions.to_csv(data_path, index=False)
    defects.to_csv(labels_path, index=False)

    logger.info("Wrote %s rows -> %s", len(transactions), data_path.relative_to(REPO_ROOT))
    logger.info("Injected %s defects -> %s", len(defects), labels_path.relative_to(REPO_ROOT))
    if not defects.empty:
        logger.info("Defect breakdown:\n%s", defects["defect_type"].value_counts().to_string())


if __name__ == "__main__":
    main()
