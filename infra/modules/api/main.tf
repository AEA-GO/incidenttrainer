# Session API: FastAPI on Lambda (Python 3.13, arm64), behind an API Gateway HTTP API.
# CloudFront sends /api/* here, so the browser only ever talks to one domain.
#
# Terraform creates the function with a placeholder; the CI job deploy-api uploads the real
# code (built from api/), so code changes never need a Terraform change.
#
# Cost: Lambda's always-free tier (1M requests, 400k GB-seconds a month); HTTP API ~$1 per
# million requests; logs kept 14 days. Cents at our volume.
#
# It starts and deletes game servers through the launcher (contract 4): its role may launch
# only from our launch templates, into the sandbox subnets, as t4g.medium or the two fallback
# sizes, with a game tag; and delete only servers tagged as games. The account guardrails
# apply on top.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  ec2_arn        = "arn:aws:ec2:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}"
  subnet_arns    = [for id in var.sandbox_subnet_ids : "${local.ec2_arn}:subnet/${id}"]
  game_sizes     = ["t4g.medium", "m7g.medium", "m6g.medium"] # decided 21 Nov
  guardrails_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/itr-guardrails"
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

  # --- The launcher (contract 4) ---
  statement {
    sid       = "LaunchOnlyGameServersFromOurTemplates"
    actions   = ["ec2:RunInstances"]
    resources = ["${local.ec2_arn}:instance/*"]
    condition {
      test     = "ArnLike"
      variable = "ec2:LaunchTemplate"
      values   = var.launch_template_arns
    }
    condition {
      test     = "StringEquals"
      variable = "ec2:InstanceType"
      values   = local.game_sizes
    }
    condition {
      test     = "Null"
      variable = "aws:RequestTag/itr-game"
      values   = ["false"]
    }
  }
  statement {
    sid       = "UseOurTemplates"
    actions   = ["ec2:RunInstances"]
    resources = var.launch_template_arns
  }
  statement {
    sid       = "OnlyTheSandboxSubnets"
    actions   = ["ec2:RunInstances"]
    resources = local.subnet_arns
  }
  statement {
    sid       = "NetworkInterfacesInTheSandboxSubnets"
    actions   = ["ec2:RunInstances"]
    resources = ["${local.ec2_arn}:network-interface/*"]
    condition {
      test     = "ArnEquals"
      variable = "ec2:Subnet"
      values   = local.subnet_arns
    }
  }
  statement {
    sid       = "SecurityGroupFromTheTemplate"
    actions   = ["ec2:RunInstances"]
    resources = ["${local.ec2_arn}:security-group/*"]
    condition {
      test     = "ArnLike"
      variable = "ec2:LaunchTemplate"
      values   = var.launch_template_arns
    }
    condition {
      test     = "Bool"
      variable = "ec2:IsLaunchTemplateResource"
      values   = ["true"]
    }
  }
  statement {
    sid       = "DisksForTheTemplatesServers"
    actions   = ["ec2:RunInstances"]
    resources = ["${local.ec2_arn}:volume/*"]
    condition {
      test     = "ArnLike"
      variable = "ec2:LaunchTemplate"
      values   = var.launch_template_arns
    }
  }
  statement {
    sid       = "OnlyOurOwnImages"
    actions   = ["ec2:RunInstances"]
    resources = ["arn:aws:ec2:${data.aws_region.current.region}::image/*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:Owner"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
  statement {
    sid       = "TagOnlyWhileLaunching"
    actions   = ["ec2:CreateTags"]
    resources = ["${local.ec2_arn}:instance/*", "${local.ec2_arn}:volume/*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["RunInstances"]
    }
  }
  statement {
    sid       = "ReadTheImageParameter" # the template's image is resolve:ssm:/itr/worlds/<world>/ami
    actions   = ["ssm:GetParameters"]
    resources = ["arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/itr/worlds/*"]
  }
  statement {
    sid       = "DeleteOnlyGameServers"
    actions   = ["ec2:TerminateInstances"]
    resources = ["${local.ec2_arn}:instance/*"]
    condition {
      test     = "Null"
      variable = "aws:ResourceTag/itr-game"
      values   = ["false"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/env"
      values   = ["main"]
    }
  }
  statement {
    sid       = "EncryptedDisks"
    actions   = ["kms:CreateGrant", "kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKeyWithoutPlaintext", "kms:ReEncrypt*"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ec2.${data.aws_region.current.region}.amazonaws.com"]
    }
  }
  statement {
    sid       = "Describe"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "${var.name}-api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# The account guardrails (region, instance sizes, no expensive networking) bind it too.
resource "aws_iam_role_policy_attachment" "guardrails" {
  role       = aws_iam_role.api.name
  policy_arn = local.guardrails_arn
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
      TABLE_NAME          = var.table_name
      ITR_LAUNCHER        = "ec2"
      ITR_SANDBOX_SUBNETS = join(",", var.sandbox_subnet_ids) # zone a first
      ITR_TEMPLATE_PREFIX = "${var.name}-"
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
