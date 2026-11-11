# ---------------------------------------------------------------------------
# Data quality validator Lambda
# Build the package first:  python scripts/build_lambda.py
# ---------------------------------------------------------------------------

variable "pandas_layer_arn" {
  description = "AWS SDK for pandas managed layer (provides pandas, numpy, boto3) for Python 3.12 x86_64 in Sydney"
  type        = string
  default     = "arn:aws:lambda:ap-southeast-2:336392948345:layer:AWSSDKPandas-Python312:32"
}

locals {
  validator_name = "${var.project_name}-${var.environment}-validator"
  validator_zip  = "${path.module}/../build/validator_lambda.zip"
}

# --- Identity: who the Lambda "is" when it runs -----------------------------

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "validator" {
  name               = local.validator_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# --- Least-privilege permissions: only what the validator actually needs ----

data "aws_iam_policy_document" "validator_permissions" {
  statement {
    sid       = "ReadRawZone"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data_lake.arn}/raw/*"]
  }

  statement {
    sid     = "WriteOutputZones"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.data_lake.arn}/validated/*",
      "${aws_s3_bucket.data_lake.arn}/rejected/*",
      "${aws_s3_bucket.data_lake.arn}/dq-reports/*",
    ]
  }

  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.validator.arn}:*"]
  }
}

resource "aws_iam_role_policy" "validator" {
  name   = "${local.validator_name}-permissions"
  role   = aws_iam_role.validator.id
  policy = data.aws_iam_policy_document.validator_permissions.json
}

# --- Logs: keep 14 days, then delete automatically (cost control) -----------

resource "aws_cloudwatch_log_group" "validator" {
  name              = "/aws/lambda/${local.validator_name}"
  retention_in_days = 14
}

# --- The function itself ------------------------------------------------------

resource "aws_lambda_function" "validator" {
  function_name    = local.validator_name
  role             = aws_iam_role.validator.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = local.validator_zip
  source_code_hash = filebase64sha256(local.validator_zip) # redeploy only when code changes
  layers           = [var.pandas_layer_arn]
  memory_size      = 1024 # pandas needs room; more memory also means more CPU
  timeout          = 120  # seconds

  environment {
    variables = {
      DATA_BUCKET = aws_s3_bucket.data_lake.bucket
      ENVIRONMENT = var.environment
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.validator,
    aws_iam_role_policy.validator,
  ]
}

output "validator_function_name" {
  value = aws_lambda_function.validator.function_name
}
