# Ingestion Contract

This document defines how transaction files enter the platform. Every data source must follow it, so the pipeline can process files automatically and reliably.

## 1. File format

| Item | Rule |
|---|---|
| Format | CSV, UTF-8, comma-separated, first row is the header |
| One file | One batch of transactions for one business date |
| Max size | 500 MB per file (larger feeds must be split into batches) |

## 2. File naming

```
transactions_<YYYY-MM-DD>_batch<NNN>.csv
```

- `YYYY-MM-DD` is the business date of the transactions.
- `NNN` is a 3-digit batch number for that date, starting at `001`.
- Example: `transactions_2026-11-08_batch001.csv`

Files that do not match this pattern are refused by the uploader.

## 3. Landing location (S3 raw zone)

```
s3://fin-dq-<env>-<account_id>/raw/year=YYYY/month=MM/day=DD/<filename>
```

- Hive-style partitions (`year=`, `month=`, `day=`) let Glue and Athena read only the dates a query needs, which makes queries faster and cheaper.
- Files in `raw/` are never edited. They are the original evidence for audits and re-processing.

## 4. Expected schema

| Column | Type | Required | Notes |
|---|---|---|---|
| transaction_id | string | Yes | Unique per transaction, e.g. `TXN-4FCACDC173464238` |
| customer_id | string | Yes | e.g. `CUST-401642` |
| merchant_id | string | Yes | e.g. `MER-08994` |
| transaction_ts | timestamp | Yes | ISO 8601 UTC, e.g. `2026-11-08T03:44:06Z` |
| amount | decimal(12,2) | Yes | Greater than 0 |
| currency | string | Yes | ISO 4217 uppercase code (AUD, USD, EUR, GBP, SGD) |
| payment_method | string | Yes | card, bank_transfer, digital_wallet, bnpl |
| payment_source | string | Yes | Source system that sent the transaction |
| status | string | Yes | approved, declined, refunded |
| country | string | Yes | ISO 3166 2-letter code |
| merchant_name | string | No | Display name |

The detailed validation rules are defined in Phase 3 (data contract YAML).

## 5. Idempotency (safe re-runs)

- Uploading the same file twice does not create a duplicate: the uploader skips files that already exist in S3.
- Replacing a file requires an explicit `--overwrite`, so corrections are deliberate and visible in the logs.
- Downstream steps must also be safe to re-run for the same date and batch.

## 6. Processing trigger (planned)

1. A file lands in `raw/`.
2. S3 sends an "Object Created" event to Amazon EventBridge.
3. EventBridge starts the Step Functions pipeline: validate (Lambda), then ETL (Glue), then catalog update.

Implemented in Phase 5.

## 7. Data handling

- Synthetic data only. No real customer information.
- Bucket blocks all public access and encrypts every object (SSE-S3).
- Lifecycle: `raw/` moves to Standard-IA after 30 days and is deleted after 365 days; `rejected/` is deleted after 90 days.
