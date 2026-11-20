# The gateway: one small server for all games, a CloudFront VPC origin for /s/*.
#
#   CloudFront /s/* ──(VPC origin, port 80)──► nginx ──► gateway-auth 127.0.0.1:9000
#                                                 └──► game servers (agent 8080, Grafana 3000, ttyd 7681)
#
# - Built from the gateway image (SSM /itr/gateway/ami, made by .github/workflows/
#   gateway-image.yml). Terraform uses the image only when it creates the server; later
#   images reach it by a root-disk swap in that workflow (same instance, same IP), so
#   `ami` is ignored here and CloudFront's VPC origin never needs updating.
# - Private platform subnet, no public IP; reaches only game servers and, through the free
#   gateway endpoints, DynamoDB and S3 (security group in modules/network).
# - Its role can read/write the game table and read runbooks from the lab bucket. Nothing
#   else: no SSM, no EC2, no logs service.
# - Settings come from user data (/etc/itr-gateway.env), so the image has none.
#
# Cost: t4g.micro ~$7/month + 8 GB gp3 ~$0.80/month. CPU credits "standard", so a stuck
# process can't run up a bill.

data "aws_region" "current" {}

data "aws_ssm_parameter" "ami" {
  name = var.ami_parameter
}

# --- Role: the game table and runbooks, nothing more ------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "gateway" {
  name               = "${var.name}-gateway"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "gateway" {
  statement {
    sid = "GameTable"
    # Reads games/keys/active games; writes heartbeats, tokens, events, progress, the end
    # (a transaction needs the rights of each action inside it).
    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:ConditionCheckItem",
    ]
    resources = [var.table_arn]
  }
  statement {
    sid       = "Runbooks"
    actions   = ["s3:GetObject"]
    resources = ["${var.labs_bucket_arn}/labs/*/runbook.json"]
  }
}

resource "aws_iam_role_policy" "gateway" {
  name   = "${var.name}-gateway"
  role   = aws_iam_role.gateway.id
  policy = data.aws_iam_policy_document.gateway.json
}

resource "aws_iam_instance_profile" "gateway" {
  name = "${var.name}-gateway"
  role = aws_iam_role.gateway.name
}

# --- The server ------------------------------------------------------------------------

resource "aws_instance" "gateway" {
  ami                         = data.aws_ssm_parameter.ami.insecure_value
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.security_group_id]
  iam_instance_profile        = aws_iam_instance_profile.gateway.name
  associate_public_ip_address = false

  user_data = <<-EOT
    #cloud-config
    write_files:
      - path: /etc/itr-gateway.env
        permissions: "0644"
        content: |
          ITR_TABLE=${var.table_name}
          ITR_LAB_BUCKET=${var.labs_bucket}
          ITR_PUBLIC_ORIGIN=${var.public_origin}
          AWS_REGION=${data.aws_region.current.region}
          AWS_DEFAULT_REGION=${data.aws_region.current.region}
  EOT

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  credit_specification {
    cpu_credits = "standard"
  }

  root_block_device {
    volume_size           = 8 # equal to the image's: a root-disk swap may not shrink it
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  tags = { Name = "${var.name}-gateway", cost = "gateway" }

  lifecycle {
    ignore_changes = [ami] # new images arrive by root-disk swap (gateway-image workflow)
  }
}
