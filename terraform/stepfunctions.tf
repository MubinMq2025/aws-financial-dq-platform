# ---------------------------------------------------------------------------
# Step Functions: the pipeline "manager". Runs the steps in order:
#
#   ValidateFile (Lambda) --> anything validated? --> CurateTransactions (Glue) --> done
#                                   \--> no --> NothingToCurate (done)
#   Any step fails (after retries) --> PipelineFailed
#
# Cost: Standard workflows bill per state transition (US$0.025 per 1,000);
# one run is about 5 transitions, so effectively free. Glue runs as before (~US$0.05).
# ---------------------------------------------------------------------------

locals {
  pipeline_name = "${var.project_name}-${var.environment}-pipeline"

  pipeline_definition = {
    Comment = "Validate a new raw file, then rebuild the curated layer"
    StartAt = "ValidateFile"
    States = {
      ValidateFile = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.validator.arn
          "Payload.$"  = "$" # pass the EventBridge event straight to the handler
        }
        ResultSelector = {
          "processed.$" = "$.Payload.processed"
          "skipped.$"   = "$.Payload.skipped"
        }
        ResultPath = "$.validation"
        Retry = [{
          # Temporary AWS-side hiccups: try again with growing waits (2s, 4s, 8s)
          ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
          IntervalSeconds = 2
          MaxAttempts     = 3
          BackoffRate     = 2
        }]
        Catch = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "PipelineFailed" }]
        Next  = "AnythingToCurate"
      }

      AnythingToCurate = {
        Type = "Choice"
        Choices = [{
          Variable  = "$.validation.processed[0]"
          IsPresent = true
          Next      = "CurateTransactions"
        }]
        Default = "NothingToCurate"
      }

      CurateTransactions = {
        Type     = "Task"
        Resource = "arn:aws:states:::glue:startJobRun.sync" # .sync = wait until the Glue job finishes
        Parameters = {
          JobName = aws_glue_job.curate.name
        }
        ResultSelector = {
          "job_run_id.$" = "$.Id"
          "state.$"      = "$.JobRunState"
        }
        ResultPath = "$.curation"
        Retry = [{
          # Only one Glue run is allowed at a time; if another is running, wait and try again (1, 2, 4, 8 min)
          ErrorEquals     = ["Glue.ConcurrentRunsExceededException"]
          IntervalSeconds = 60
          MaxAttempts     = 4
          BackoffRate     = 2
        }]
        Catch = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "PipelineFailed" }]
        Next  = "PipelineSucceeded"
      }

      NothingToCurate   = { Type = "Succeed" }
      PipelineSucceeded = { Type = "Succeed" }

      PipelineFailed = {
        Type  = "Fail"
        Error = "PipelineFailed"
        Cause = "Validation or curation failed after retries. Open the execution to see the failed step."
      }
    }
  }
}

# --- Identity for the state machine: may only call our Lambda and our Glue job ---

data "aws_iam_policy_document" "sfn_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pipeline" {
  name               = local.pipeline_name
  assume_role_policy = data.aws_iam_policy_document.sfn_assume_role.json
}

data "aws_iam_policy_document" "pipeline_permissions" {
  statement {
    sid       = "InvokeValidator"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.validator.arn]
  }

  statement {
    sid       = "RunCurateJob"
    actions   = ["glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:BatchStopJobRun"]
    resources = [aws_glue_job.curate.arn]
  }
}

resource "aws_iam_role_policy" "pipeline" {
  name   = "${local.pipeline_name}-permissions"
  role   = aws_iam_role.pipeline.id
  policy = data.aws_iam_policy_document.pipeline_permissions.json
}

# --- The state machine --------------------------------------------------------

resource "aws_sfn_state_machine" "pipeline" {
  name       = local.pipeline_name
  role_arn   = aws_iam_role.pipeline.arn
  type       = "STANDARD" # keeps a visual history of every run, ideal for auditing
  definition = jsonencode(local.pipeline_definition)

  depends_on = [aws_iam_role_policy.pipeline]
}

output "pipeline_state_machine" {
  value = aws_sfn_state_machine.pipeline.name
}
