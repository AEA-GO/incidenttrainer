# Cleanup, the second safety net (api/src/itr_api/cleanup.py): every 5 minutes EventBridge
# Scheduler invokes the API function with {"itr_cleanup": true}. Same code and role as the
# API (it reuses Finish and the launcher); alerts go to the itr-alerts topic (bootstrap),
# i.e. Telegram + email.
#
# Cost: ~8,640 runs a month. Scheduler's free tier is 14 million invocations a month, and
# ~1 s of a 512 MB Lambda per run stays well inside Lambda's free tier: $0.

locals {
  alerts_topic_arn = "arn:aws:sns:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:itr-alerts"
}

data "aws_iam_policy_document" "cleanup_alerts" {
  statement {
    actions   = ["sns:Publish"]
    resources = [local.alerts_topic_arn]
  }
}

resource "aws_iam_role_policy" "cleanup_alerts" {
  name   = "${var.name}-cleanup-alerts"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.cleanup_alerts.json
}

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "cleanup_scheduler" {
  name               = "${var.name}-cleanup-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

data "aws_iam_policy_document" "cleanup_scheduler" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.api.arn]
  }
}

resource "aws_iam_role_policy" "cleanup_scheduler" {
  name   = "${var.name}-cleanup-scheduler"
  role   = aws_iam_role.cleanup_scheduler.id
  policy = data.aws_iam_policy_document.cleanup_scheduler.json
}

resource "aws_scheduler_schedule" "cleanup" {
  name                = "${var.name}-cleanup"
  schedule_expression = "rate(5 minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.api.arn
    role_arn = aws_iam_role.cleanup_scheduler.arn
    input    = jsonencode({ itr_cleanup = true })

    # The next run is 5 minutes away anyway; a retry could overlap it.
    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}
