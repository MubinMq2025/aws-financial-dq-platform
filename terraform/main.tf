# Look up the current account ID so the bucket name is globally unique
data "aws_caller_identity" "current" {}

locals {
  bucket_name = "${var.project_name}-${var.environment}-${data.aws_caller_identity.current.account_id}"
}

# The data lake bucket
resource "aws_s3_bucket" "data_lake" {
  bucket        = local.bucket_name
  force_destroy = var.environment == "dev" # allow easy cleanup in dev only
}

# Block all public access (essential for financial data)
resource "aws_s3_bucket_public_access_block" "data_lake" {
  bucket                  = aws_s3_bucket.data_lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Encrypt every file at rest
resource "aws_s3_bucket_server_side_encryption_configuration" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Data lake zones (S3 "folders" are just empty objects ending in /)
locals {
  zones = ["raw", "validated", "rejected", "curated", "dq-reports", "athena-results"]
}

resource "aws_s3_object" "zones" {
  for_each = toset(local.zones)
  bucket   = aws_s3_bucket.data_lake.id
  key      = "${each.value}/"
}
