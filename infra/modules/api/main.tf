# Session API: FastAPI on Lambda (Python 3.13, arm64), behind an API Gateway HTTP API.
# CloudFront sends /api/* here, so the browser only ever talks to one domain.
#
# Terraform creates the function with a placeholder; the CI job deploy-api uploads the real
# code (built from api/), so code changes never need a Terraform change.
#
# Cost: Lambda's always-free tier (1M requests, 400k GB-seconds a month); HTTP API ~$1 per
# million requests; logs kept 14 days. Cents at our volume.

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "${var.name}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${var.name}-api"
  retention_in_days = 14
}

data "aws_iam_policy_document" "api" {
  statement {
    sid = "Table"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
      "dynamodb:Query", "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem", "dynamodb:ConditionCheckItem",
    ]
    resources = [var.table_arn]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "${var.name}-api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# Placeholder until the first deploy-api run (same handler path as the real app).
data "archive_file" "placeholder" {
  type        = "zip"
  output_path = "${path.module}/.build/placeholder.zip"

  source {
    filename = "itr_api/main.py"
    content  = <<-PY
      def handler(event, context):
          return {
              "statusCode": 503,
              "headers": {"content-type": "application/json"},
              "body": '{"status": "not deployed yet"}',
          }
    PY
  }
}

resource "aws_lambda_function" "api" {
  function_name = "${var.name}-api"
  role          = aws_iam_role.api.arn
  runtime       = "python3.13"
  architectures = ["arm64"]
  handler       = "itr_api.main.handler"
  filename      = data.archive_file.placeholder.output_path
  memory_size   = 512 # more memory = more CPU = faster cold starts; still inside the free tier
  timeout       = 15

  environment {
    variables = {
      TABLE_NAME = var.table_name
    }
  }

  # deploy-api owns the code; Terraform owns everything else.
  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api]
}

# --- HTTP API --------------------------------------------------------------------

resource "aws_apigatewayv2_api" "api" {
  name          = "${var.name}-api"
  protocol_type = "HTTP"
  description   = "IncidentTrainer session API (reached through CloudFront /api/*)"
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "api" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "ANY /api/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true

  # Caps abuse (and cost) for the whole API: 20 requests/s on average, bursts of 50.
  default_route_settings {
    throttling_rate_limit  = 20
    throttling_burst_limit = 50
  }
}

resource "aws_lambda_permission" "apigateway" {
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}
