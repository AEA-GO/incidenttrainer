# itr-gha-image: the labs repo's image job (Packer). Only pushes to the labs repo's
# main branch (and manual runs on main) may use it. It can build AMIs in the default
# VPC, publish the AMI id in SSM, and prune old AMIs; it can't stop or terminate
# anything Packer didn't start, so it can't touch game servers.

locals {
  labs_repo = "${var.github_org}@${var.github_org_id}/${var.labs_repo}@${var.labs_repo_id}"
}

data "aws_iam_policy_document" "image_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${local.labs_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "image" {
  name                 = "itr-gha-image"
  assume_role_policy   = data.aws_iam_policy_document.image_trust.json
  max_session_duration = 7200 # a build takes ~30 min; room for slow days
}

data "aws_iam_policy_document" "image" {
  statement {
    sid = "PackerBuild"
    actions = [
      "ec2:Describe*",
      "ec2:RunInstances",
      "ec2:CreateKeyPair",
      "ec2:DeleteKeyPair",
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:CreateTags",
      "ec2:CreateVolume",
      "ec2:AttachVolume",
      "ec2:DetachVolume",
      "ec2:DeleteVolume",
      "ec2:CreateSnapshot",
      "ec2:CreateImage",
      "ec2:RegisterImage",
      "ec2:ModifyImageAttribute",
      "ec2:ModifySnapshotAttribute",
      "ec2:ModifyInstanceAttribute",
      "ec2:GetPasswordData",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "OnlyBuildServers"
    actions   = ["ec2:StopInstances", "ec2:TerminateInstances"]
    resources = ["arn:aws:ec2:${var.region}:${local.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/cost"
      values   = ["image-build"]
    }
  }

  statement {
    sid       = "PruneOnlyWorldImages"
    actions   = ["ec2:DeregisterImage", "ec2:DeleteSnapshot"]
    resources = ["*"]
    condition {
      test     = "Null"
      variable = "ec2:ResourceTag/itr-world"
      values   = ["false"]
    }
  }

  statement {
    sid       = "EncryptedVolumes"
    actions   = ["kms:CreateGrant", "kms:Decrypt", "kms:DescribeKey", "kms:Encrypt", "kms:GenerateDataKey*", "kms:ReEncrypt*"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ec2.${var.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "PublishAmiId"
    actions   = ["ssm:GetParameter", "ssm:PutParameter", "ssm:AddTagsToResource"]
    resources = ["arn:aws:ssm:${var.region}:${local.account_id}:parameter/itr/worlds/*"]
  }
}

resource "aws_iam_role_policy" "image" {
  name   = "itr-image-build"
  role   = aws_iam_role.image.id
  policy = data.aws_iam_policy_document.image.json
}

resource "aws_iam_role_policy_attachment" "image_guardrails" {
  role       = aws_iam_role.image.name
  policy_arn = aws_iam_policy.guardrails.arn
}
