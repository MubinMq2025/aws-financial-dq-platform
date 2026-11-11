# ---------------------------------------------------------------------------
# Glue Data Catalog: table DESCRIPTIONS (metadata) for data that lives in S3.
# Athena reads these to know where files are and how to read them.
#
# Partition projection: instead of running crawlers or registering every new
# day by hand, Athena works out the year=/month=/day= folders from a pattern.
# New days become queryable automatically, at no extra cost.
# ---------------------------------------------------------------------------

resource "aws_glue_catalog_database" "dq" {
  name        = replace("${var.project_name}_${var.environment}", "-", "_") # fin_dq_dev
  description = "Financial data quality platform (${var.environment})"
}

locals {
  s3_base = "s3://${aws_s3_bucket.data_lake.bucket}"

  partition_projection = {
    "projection.enabled"      = "true"
    "projection.year.type"    = "integer"
    "projection.year.range"   = "2025,2030"
    "projection.month.type"   = "integer"
    "projection.month.range"  = "1,12"
    "projection.month.digits" = "2"
    "projection.day.type"     = "integer"
    "projection.day.range"    = "1,31"
    "projection.day.digits"   = "2"
  }

  # Curated rows have proper types (set by the Glue job)
  curated_columns = [
    ["transaction_id", "string"],
    ["customer_id", "string"],
    ["merchant_id", "string"],
    ["transaction_ts", "timestamp"],
    ["amount", "decimal(12,2)"],
    ["currency", "string"],
    ["payment_method", "string"],
    ["payment_source", "string"],
    ["status", "string"],
    ["country", "string"],
    ["merchant_name", "string"],
    ["source_file", "string"],
    ["curated_at", "timestamp"],
  ]

  # Rejected rows are kept exactly as received (all text) plus the failed rule IDs
  rejected_columns = [
    "transaction_id", "customer_id", "merchant_id", "transaction_ts", "amount", "currency",
    "payment_method", "payment_source", "status", "country", "merchant_name", "rejection_reasons",
  ]
}

# --- Rejected rows (CSV) ------------------------------------------------------

resource "aws_glue_catalog_table" "rejected_transactions" {
  name          = "rejected_transactions"
  database_name = aws_glue_catalog_database.dq.name
  description   = "Rows that failed one or more data quality rules"
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(local.partition_projection, {
    "classification"            = "csv"
    "skip.header.line.count"    = "1"
    "storage.location.template" = "${local.s3_base}/rejected/year=$${year}/month=$${month}/day=$${day}"
  })

  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }

  storage_descriptor {
    location      = "${local.s3_base}/rejected/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.serde2.OpenCSVSerde"
      parameters = {
        "separatorChar" = ","
        "quoteChar"     = "\""
        "escapeChar"    = "\\"
      }
    }

    dynamic "columns" {
      for_each = local.rejected_columns
      content {
        name = columns.value
        type = "string"
      }
    }
  }
}

# --- Data quality reports (JSON, one object per line) -------------------------

resource "aws_glue_catalog_table" "dq_reports" {
  name          = "dq_reports"
  database_name = aws_glue_catalog_database.dq.name
  description   = "One data quality report per validated file"
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(local.partition_projection, {
    "classification"            = "json"
    "storage.location.template" = "${local.s3_base}/dq-reports/year=$${year}/month=$${month}/day=$${day}"
  })

  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }

  storage_descriptor {
    location      = "${local.s3_base}/dq-reports/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }

    columns {
      name = "source_file"
      type = "string"
    }
    columns {
      name = "contract_version"
      type = "int"
    }
    columns {
      name = "run_timestamp_utc"
      type = "string"
    }
    columns {
      name = "status"
      type = "string"
    }
    columns {
      name = "total_rows"
      type = "int"
    }
    columns {
      name = "valid_rows"
      type = "int"
    }
    columns {
      name = "rejected_rows"
      type = "int"
    }
    columns {
      name = "reject_rate"
      type = "double"
    }
    columns {
      name = "rule_failures"
      type = "array<struct<rule_id:string,rule_name:string,failed_rows:int>>"
    }
    columns {
      name = "warnings"
      type = "array<struct<rule_id:string,message:string>>"
    }
  }
}

# --- Curated transactions (Parquet, written by the Glue job) -----------------

resource "aws_glue_catalog_table" "curated_transactions" {
  name          = "curated_transactions"
  database_name = aws_glue_catalog_database.dq.name
  description   = "Validated, typed and de-duplicated transactions: the main table for analytics"
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(local.partition_projection, {
    "classification"            = "parquet"
    "storage.location.template" = "${local.s3_base}/curated/transactions/year=$${year}/month=$${month}/day=$${day}"
  })

  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }

  storage_descriptor {
    location      = "${local.s3_base}/curated/transactions/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = local.curated_columns
      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }
}

output "glue_database_name" {
  value = aws_glue_catalog_database.dq.name
}
