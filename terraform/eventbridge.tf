# ---------------------------------------------------------------------------
# Event-driven trigger: a new file in raw/ automatically starts the pipeline.
#
#   S3 (Object Created) --> EventBridge rule (only raw/ keys) --> Step Functions pipeline
#
# Only raw/ is matched, so the pipeline's own outputs (validated/, rejected/, ...)
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

# 3. EventBridge needs its own identity to start the state machine
data "aws_iam_policy_document" "events_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "events_start_pipeline" {
  name               = "${var.project_name}-${var.environment}-events-start-pipeline"
  assume_role_policy = data.aws_iam_policy_document.events_assume_role.json
}

data "aws_iam_policy_document" "events_start_pipeline" {
  statement {
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.pipeline.arn]
  }
}

resource "aws_iam_role_policy" "events_start_pipeline" {
  name   = "start-pipeline"
  role   = aws_iam_role.events_start_pipeline.id
  policy = data.aws_iam_policy_document.events_start_pipeline.json
}

# 4. Send matching events to the pipeline (previously: straight to the Lambda)
resource "aws_cloudwatch_event_target" "pipeline" {
  rule      = aws_cloudwatch_event_rule.raw_file_arrived.name
  target_id = "pipeline-state-machine"
  arn       = aws_sfn_state_machine.pipeline.arn
  role_arn  = aws_iam_role.events_start_pipeline.arn
}
