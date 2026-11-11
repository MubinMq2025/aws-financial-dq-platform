"""
AWS Glue ETL job: validated/ (CSV) -> curated/transactions/ (Parquet)

Extract   Read every validated CSV, across all days, in one go.
Transform 1. Turn text columns into proper types (timestamp, decimal).
          2. Remove duplicate transaction_ids ACROSS days (the Lambda only
             sees one file at a time, so it cannot catch these).
          3. Add lineage columns: which file each row came from, and when it was curated.
Load      Write Parquet, partitioned by year/month/day, replacing the previous output.

Re-running the job rebuilds curated/ from validated/, so the result is always the
same for the same input (idempotent). Fine for our data size; a very large dataset
would process only new partitions instead (incremental loading).

Glue passes arguments like:  --DATA_BUCKET fin-dq-dev-046749917047
"""

from __future__ import annotations

import sys

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

TIMESTAMP_FORMAT = "yyyy-MM-dd'T'HH:mm:ss'Z'"


def read_validated(spark: SparkSession, path: str) -> DataFrame:
    """Read validated CSVs. year/month/day come from the folder names."""
    return (
        spark.read.option("header", "true")
        .option("escape", '"')          # pandas writes quotes inside values as ""
        .option("inferSchema", "false")  # keep everything as text; we cast explicitly below
        .csv(path)
        .withColumn("source_file", F.input_file_name())
    )


def curate(df: DataFrame) -> DataFrame:
    """Type, deduplicate and enrich validated transactions."""
    typed = (
        df.withColumn("transaction_ts", F.to_timestamp("transaction_ts", TIMESTAMP_FORMAT))
        .withColumn("amount", F.col("amount").cast("decimal(12,2)"))
    )

    # Keep the earliest record for each transaction_id across all days
    first_seen = Window.partitionBy("transaction_id").orderBy(F.col("transaction_ts"), F.col("source_file"))
    deduped = (
        typed.withColumn("_row_number", F.row_number().over(first_seen))
        .filter(F.col("_row_number") == 1)
        .drop("_row_number")
    )

    return deduped.withColumn("curated_at", F.current_timestamp()).select(
        "transaction_id", "customer_id", "merchant_id", "transaction_ts", "amount", "currency",
        "payment_method", "payment_source", "status", "country", "merchant_name",
        "source_file", "curated_at", "year", "month", "day",
    )


def write_curated(df: DataFrame, path: str) -> None:
    (
        df.write.mode("overwrite")
        .partitionBy("year", "month", "day")
        .parquet(path)
    )


def main() -> None:
    from awsglue.utils import getResolvedOptions  # only available inside AWS Glue

    args = getResolvedOptions(sys.argv, ["JOB_NAME", "DATA_BUCKET"])
    bucket = args["DATA_BUCKET"]

    spark = (
        SparkSession.builder.appName(args["JOB_NAME"])
        # Keep partition values as text like "08" (not the number 8)
        .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
        .getOrCreate()
    )

    raw = read_validated(spark, f"s3://{bucket}/validated/")
    curated = curate(raw)

    input_rows, output_rows = raw.count(), curated.count()
    print(f"Input rows: {input_rows} | Curated rows: {output_rows} | Cross-day duplicates removed: {input_rows - output_rows}")

    write_curated(curated, f"s3://{bucket}/curated/transactions/")
    print("Curated data written")


if __name__ == "__main__":
    main()
