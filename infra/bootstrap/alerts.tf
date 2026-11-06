# Budget, anomaly and (later) CloudWatch alarm alerts -> SNS -> Telegram Alerts topic + email.
# Cost: SNS, Lambda and Budgets stay inside their free tiers (the first 2 budgets are free).

resource "aws_sns_topic" "alerts" {
  name = "itr-alerts"
}

data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid       = "AllowBudgetsAndCostAnomaly"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com", "costalerts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }

  statement {
    sid       = "AllowCloudWatchAlarms"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }

  statement {
    sid       = "AllowAccountPrincipals"
    actions   = ["sns:Publish", "sns:Subscribe", "sns:GetTopicAttributes"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email # confirm the subscription from the inbox
}

# --- Monthly budget: alerts at $25 / $40 / $50 actual, and $50 forecast -----------

resource "aws_budgets_budget" "monthly" {
  name         = "itr-monthly"
  budget_type  = "COST"
  limit_amount = tostring(max(var.budget_alerts_usd...))
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = var.budget_alerts_usd
    content {
      comparison_operator       = "GREATER_THAN"
      threshold                 = notification.value
      threshold_type            = "ABSOLUTE_VALUE"
      notification_type         = "ACTUAL"
      subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
    }
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = max(var.budget_alerts_usd...)
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "FORECASTED"
    subscriber_sns_topic_arns  = [aws_sns_topic.alerts.arn]
    subscriber_email_addresses = [var.alert_email]
  }

  depends_on = [aws_sns_topic_policy.alerts]
}

# --- Cost anomaly detection ----------------------------------------------------
# This account already has a monitor: pass its ARN in existing_anomaly_monitor_arn.

resource "aws_ce_anomaly_monitor" "services" {
  count             = var.existing_anomaly_monitor_arn == "" ? 1 : 0
  name              = "itr-services"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "alerts" {
  name      = "itr-anomalies"
  frequency = "IMMEDIATE"
  monitor_arn_list = [
    var.existing_anomaly_monitor_arn != "" ? var.existing_anomaly_monitor_arn : aws_ce_anomaly_monitor.services[0].arn
  ]

  subscriber {
    type    = "SNS"
    address = aws_sns_topic.alerts.arn
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = ["5"]
    }
  }

  depends_on = [aws_sns_topic_policy.alerts]
}

# --- Telegram relay -------------------------------------------------------------
# The token parameter is created by hand first (never in Git or Terraform state):
#   aws ssm put-parameter --name /itr/telegram/bot_token --type SecureString --value "$TG_TOKEN"

data "archive_file" "telegram_relay" {
  type        = "zip"
  source_file = "${path.module}/telegram_relay.py"
  output_path = "${path.module}/.build/telegram_relay.zip"
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "telegram_relay" {
  name               = "itr-telegram-relay"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "telegram_relay" {
  statement {
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.region}:${local.account_id}:parameter${var.telegram_token_parameter}"]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.telegram_relay.arn}:*"]
  }
}

resource "aws_iam_role_policy" "telegram_relay" {
  name   = "itr-telegram-relay"
  role   = aws_iam_role.telegram_relay.id
  policy = data.aws_iam_policy_document.telegram_relay.json
}

resource "aws_cloudwatch_log_group" "telegram_relay" {
  name              = "/aws/lambda/itr-telegram-relay"
  retention_in_days = 14
}

resource "aws_lambda_function" "telegram_relay" {
  function_name    = "itr-telegram-relay"
  role             = aws_iam_role.telegram_relay.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "telegram_relay.handler"
  filename         = data.archive_file.telegram_relay.output_path
  source_code_hash = data.archive_file.telegram_relay.output_base64sha256
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      TOKEN_PARAMETER = var.telegram_token_parameter
      CHAT_ID         = var.telegram_chat_id
      THREAD_ID       = var.telegram_thread_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.telegram_relay, aws_iam_role_policy.telegram_relay]
}

resource "aws_lambda_permission" "sns" {
  statement_id  = "AllowSns"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.telegram_relay.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alerts.arn
}

resource "aws_sns_topic_subscription" "telegram" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.telegram_relay.arn

  depends_on = [aws_lambda_permission.sns]
}
