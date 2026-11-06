# Guardrails that work without AWS Organizations (no SCPs on a standalone account).
# Attached to the developers group and to the CI apply roles.

data "aws_iam_policy_document" "guardrails" {
  statement {
    sid    = "OnlyOurRegion"
    effect = "Deny"
    not_actions = [
      "iam:*", "sts:*", "cloudfront:*", "route53:*", "route53domains:*", "acm:*",
      "budgets:*", "ce:*", "cur:*", "pricing:*", "support:*", "health:*",
      "account:*", "billing:*", "freetier:*", "signin:*", "s3:ListAllMyBuckets",
      "servicequotas:*", "trustedadvisor:*",
    ]
    resources = ["*"]
    condition {
      test     = "StringNotEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }

  statement {
    sid       = "OnlySmallGravitonInstances"
    effect    = "Deny"
    actions   = ["ec2:RunInstances"]
    resources = ["arn:aws:ec2:*:*:instance/*"]
    condition {
      test     = "StringNotEquals"
      variable = "ec2:InstanceType"
      values   = ["t4g.nano", "t4g.micro", "t4g.small", "t4g.medium"]
    }
  }

  statement {
    sid    = "NoExpensiveNetworking"
    effect = "Deny"
    actions = [
      "ec2:CreateNatGateway",
      "ec2:AllocateAddress",
      "ec2:EnableFastSnapshotRestores",
      "elasticloadbalancing:CreateLoadBalancer",
      "eks:CreateCluster",
      "rds:CreateDBInstance",
      "rds:CreateDBCluster",
      "globalaccelerator:CreateAccelerator",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "OnlyFreeGatewayEndpoints"
    effect    = "Deny"
    actions   = ["ec2:CreateVpcEndpoint"]
    resources = ["arn:aws:ec2:*:*:vpc-endpoint/*"]
    condition {
      test     = "StringNotEquals"
      variable = "ec2:VpceServiceName"
      values = [
        "com.amazonaws.${var.region}.s3",
        "com.amazonaws.${var.region}.dynamodb",
      ]
    }
  }
}

resource "aws_iam_policy" "guardrails" {
  name        = "itr-guardrails"
  description = "Region, instance-size and cost guardrails for humans and CI"
  policy      = data.aws_iam_policy_document.guardrails.json
}
