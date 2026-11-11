# ---------------------------------------------------------------------------
# Email alerts via SNS
#
#   Step Functions run FAILED / TIMED_OUT / ABORTED --> EventBridge rule --> SNS --> email
#   Reject rate above 10% on any file               --> CloudWatch alarm --> SNS --> email
#   File-level warnings (volume, high reject rate)   --> CloudWatch alarm --> SNS --> email
#
# Your email address goes in terraform/terraform.tfvars (git-ignored, so it never
# reaches the public repo). Cost: email notifications are free for the first 1,000/month;
# alarms are within the always-free 10 alarms.
# ---------------------------------------------------------------------------

variable "alert_email" {
  description = "Email address that receives pipeline alerts (set in terraform.tfvars)"
  type        = string
}

variable "reject_rate_alert_percent" {
  description = "Alert when any file's reject rate goes above this percentage"
  type        = number
  default     = 10
}

resource "aws_sns_topic" "alerts" {
  name = "${var.project_name}-${var.environment}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email # AWS sends a confirmation email; click the link once
}

# Only EventBridge and CloudWatch alarms from this account may publish to the topic
data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid       = "AllowEventBridgeAndCloudWatch"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com", "cloudwatch.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

# --- Alert 1: the pipeline failed -------------------------------------------

resource "aws_cloudwatch_event_rule" "pipeline_failed" {
  name        = "${var.project_name}-${var.environment}-pipeline-failed"
  description = "The data quality pipeline did not finish successfully"

  event_pattern = jsonencode({
    source        = ["aws.states"]
    "detail-type" = ["Step Functions Execution Status Change"]
    detail = {
      stateMachineArn = [aws_sfn_state_machine.pipeline.arn]
      status          = ["FAILED", "TIMED_OUT", "ABORTED"]
    }
  })
}

resource "aws_cloudwatch_event_target" "pipeline_failed_email" {
  rule      = aws_cloudwatch_event_rule.pipeline_failed.name
  target_id = "alerts-topic"
  arn       = aws_sns_topic.alerts.arn

  # Turn the raw event into a readable email
  input_transformer {
    input_paths = {
      status    = "$.detail.status"
      execution = "$.detail.name"
      time      = "$.time"
    }
    # Written as a plain quoted string: jsonencode() would escape < and > (as \u003c),
    # and EventBridge would no longer recognise the <placeholders>.
    input_template = "\"[fin-dq ${var.environment}] Pipeline <status> at <time> (execution <execution>). Open Step Functions, ${aws_sfn_state_machine.pipeline.name}, Executions to see the failed step.\""
  }
}

# --- Alert 2: a file's reject rate is too high --------------------------------

resource "aws_cloudwatch_metric_alarm" "high_reject_rate" {
  alarm_name          = "${var.project_name}-${var.environment}-high-reject-rate"
  alarm_description   = "A file had more than ${var.reject_rate_alert_percent}% of rows rejected. Check v_rule_failures_daily in Athena."
  namespace           = "FinDQ"
  metric_name         = "RejectRatePercent"
  dimensions          = { Environment = var.environment }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.reject_rate_alert_percent
  treat_missing_data  = "notBreaching" # no files arriving is not an alarm
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

# --- Alert 3: file-level warnings (unusual volume, high reject rate) ----------

resource "aws_cloudwatch_metric_alarm" "file_warnings" {
  alarm_name          = "${var.project_name}-${var.environment}-file-warnings"
  alarm_description   = "A file raised a data quality warning (e.g. DQ100 unusual row count). Check dq_reports in Athena."
  namespace           = "FinDQ"
  metric_name         = "Warnings"
  dimensions          = { Environment = var.environment }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
