# ---------------------------------------------------------------------------
# CloudWatch dashboard: pipeline health on one page.
#
# Data quality metrics (namespace FinDQ) come from the Lambda's log lines in
# Embedded Metric Format. Lambda and Step Functions metrics are built into AWS.
# Cost: dashboards (first 3) and our 4 custom metrics fall within CloudWatch's
# always-free allowance (10 custom metrics).
# ---------------------------------------------------------------------------

locals {
  dq_dimensions = ["Environment", var.environment]
}

resource "aws_cloudwatch_dashboard" "pipeline" {
  dashboard_name = "${var.project_name}-${var.environment}-pipeline-health"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Reject rate per file (%)"
          region  = var.aws_region
          view    = "timeSeries"
          stat    = "Maximum"
          period  = 300
          metrics = [concat(["FinDQ", "RejectRatePercent"], local.dq_dimensions)]
          annotations = {
            horizontal = [{ label = "Alert threshold (10%)", value = 10, color = "#d62728" }]
          }
          yAxis = { left = { min = 0 } }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Rows processed vs rejected"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            concat(["FinDQ", "TotalRows"], local.dq_dimensions),
            concat(["FinDQ", "RejectedRows"], local.dq_dimensions),
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 8
        height = 6
        properties = {
          title  = "Pipeline runs"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", aws_sfn_state_machine.pipeline.arn],
            ["AWS/States", "ExecutionsFailed", "StateMachineArn", aws_sfn_state_machine.pipeline.arn],
          ]
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 6
        width  = 8
        height = 6
        properties = {
          title  = "Validator Lambda: invocations and errors"
          region = var.aws_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.validator.function_name],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.validator.function_name],
          ]
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 6
        width  = 8
        height = 6
        properties = {
          title   = "Validator Lambda: duration (ms)"
          region  = var.aws_region
          view    = "timeSeries"
          stat    = "Maximum"
          period  = 300
          metrics = [["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.validator.function_name]]
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 12
        width  = 24
        height = 6
        properties = {
          title  = "Latest validated files"
          region = var.aws_region
          view   = "table"
          query  = "SOURCE '${aws_cloudwatch_log_group.validator.name}' | filter event = 'file_validated' | fields @timestamp, source_key, status, TotalRows, RejectedRows, RejectRatePercent | sort @timestamp desc | limit 20"
        }
      },
    ]
  })
}

output "dashboard_url" {
  value = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards/dashboard/${aws_cloudwatch_dashboard.pipeline.dashboard_name}"
}
