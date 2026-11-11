# ---------------------------------------------------------------------------
# Event-driven trigger: a new file in raw/ automatically starts validation.
#
#   S3 (Object Created) --> EventBridge rule (only raw/ keys) --> Lambda validator
#
# Only raw/ is matched, so the Lambda's own outputs (validated/, rejected/, ...)
# never re-trigger it. No infinite loops.
# Cost: S3 -> EventBridge events are free; rule matching is free for AWS events.
# ---------------------------------------------------------------------------

# 1. Tell S3 to send all its object events to EventBridge
resource "aws_s3_bucket_notification" "to_eventbridge" {
  bucket      = aws_s3_bucket.data_lake.id
  eventbridge = true
}

# 2. A rule that matches only "new file in raw/" events for our bucket
resource "aws_cloudwatch_event_rule" "raw_file_arrived" {
  name        = "${var.project_name}-${var.environment}-raw-file-arrived"
  description = "A new transaction file landed in the raw zone"

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.data_lake.bucket] }
      object = { key = [{ prefix = "raw/" }] }
    }
  })
}

# 3. Send matching events to the validator Lambda
resource "aws_cloudwatch_event_target" "validator" {
  rule      = aws_cloudwatch_event_rule.raw_file_arrived.name
  target_id = "validator-lambda"
  arn       = aws_lambda_function.validator.arn
}

# 4. Allow EventBridge (and only this rule) to invoke the Lambda
resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowEventBridgeRawFileArrived"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.validator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.raw_file_arrived.arn
}
