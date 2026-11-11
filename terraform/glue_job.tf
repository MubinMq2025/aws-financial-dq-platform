# ---------------------------------------------------------------------------
# Glue ETL job: validated/ (CSV) -> curated/transactions/ (Parquet)
# Cost: 2 workers x ~2 min per run ~= US$0.03 per run (billed per second, 1 min minimum)
# ---------------------------------------------------------------------------

locals {
  glue_job_name = "${var.project_name}-${var.environment}-curate-transactions"
}

# --- The PySpark script, uploaded to S3 (Glue runs scripts from S3) ----------

resource "aws_s3_object" "curate_script" {
  bucket = aws_s3_bucket.data_lake.id
  key    = "glue-scripts/curate_transactions.py"
  source = "${path.module}/../glue/curate_transactions.py"
  etag   = filemd5("${path.module}/../glue/curate_transactions.py") # re-upload only when the script changes
}

# --- Identity for the Glue job -----------------------------------------------

data "aws_iam_policy_document" "glue_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue_curate" {
  name               = local.glue_job_name
  assume_role_policy = data.aws_iam_policy_document.glue_assume_role.json
}

# AWS-managed baseline for Glue (its own logs and internals). It does NOT grant access to our bucket.
resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue_curate.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

# Least-privilege access to our bucket: read validated + script, write curated only
data "aws_iam_policy_document" "glue_curate_s3" {
  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data_lake.arn]
  }

  statement {
    sid     = "ReadInputsAndScript"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.data_lake.arn}/validated/*",
      "${aws_s3_bucket.data_lake.arn}/glue-scripts/*",
      "${aws_s3_bucket.data_lake.arn}/curated/*",
    ]
  }

  statement {
    sid     = "WriteCurated"
    actions = ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = [
      "${aws_s3_bucket.data_lake.arn}/curated/*",
      # When Glue's S3 layer empties a folder during overwrite, it writes a
      # "folder placeholder" object named <folder>_$folder$ next to it.
      "${aws_s3_bucket.data_lake.arn}/curated_$folder$",
    ]
  }
}

resource "aws_iam_role_policy" "glue_curate_s3" {
  name   = "${local.glue_job_name}-s3"
  role   = aws_iam_role.glue_curate.id
  policy = data.aws_iam_policy_document.glue_curate_s3.json
}

# --- The job ------------------------------------------------------------------

resource "aws_glue_job" "curate" {
  name              = local.glue_job_name
  description       = "Type, deduplicate across days, and write validated transactions as partitioned Parquet"
  role_arn          = aws_iam_role.glue_curate.arn
  glue_version      = "5.0"
  worker_type       = "G.1X" # smallest standard Spark worker
  number_of_workers = 2      # minimum allowed
  timeout           = 10     # minutes; stops a stuck job from running up costs
  max_retries       = 0

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.data_lake.bucket}/${aws_s3_object.curate_script.key}"
    python_version  = "3"
  }

  default_arguments = {
    "--DATA_BUCKET"  = aws_s3_bucket.data_lake.bucket
    "--job-language" = "python"
  }

  execution_property {
    max_concurrent_runs = 1 # two runs at once could overwrite each other's output
  }
}

output "glue_job_name" {
  value = aws_glue_job.curate.name
}
