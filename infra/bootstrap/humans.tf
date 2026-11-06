# Groups for people. Create the users themselves in the console (name, console
# password, forced MFA) and add them to a group: personal data stays out of Git.
# People use `aws login` (AWS CLI 2.32+) for short-lived CLI credentials: no access keys.
# After the first apply: add yourself to "admins", then detach the AdministratorAccess
# policy you attached to your user by hand.

resource "aws_iam_group" "admins" {
  name = "admins"
}

resource "aws_iam_group_policy_attachment" "admins" {
  group      = aws_iam_group.admins.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

resource "aws_iam_group" "developers" {
  name = "developers"
}

resource "aws_iam_group_policy_attachment" "developers_readonly" {
  group      = aws_iam_group.developers.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_group_policy_attachment" "developers_guardrails" {
  group      = aws_iam_group.developers.name
  policy_arn = aws_iam_policy.guardrails.arn
}

data "aws_iam_policy_document" "developers" {
  statement {
    sid       = "AwsLoginForCli"
    actions   = ["signin:AuthorizeOAuth2Access", "signin:CreateOAuth2Token"]
    resources = ["*"]
  }

  statement {
    sid = "ManageOwnPasswordAndMfa"
    actions = [
      "iam:ChangePassword", "iam:GetUser", "iam:CreateVirtualMFADevice",
      "iam:EnableMFADevice", "iam:ResyncMFADevice", "iam:ListMFADevices",
      "iam:DeleteVirtualMFADevice", "iam:DeactivateMFADevice",
    ]
    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/$${aws:username}",
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:mfa/*",
    ]
  }

  statement {
    sid       = "TerraformStateLockForLaptopPlan"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/*.tflock"]
  }

  statement {
    sid       = "SshIntoSandboxesThroughEice"
    actions   = ["ec2-instance-connect:OpenTunnel", "ec2-instance-connect:SendSSHPublicKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/env"
      values   = ["main"]
    }
  }

  # Test right after bootstrap with a developer user: if `aws login` sessions are
  # denied by this rule, change BoolIfExists to Bool (then only sessions that say
  # "no MFA" are denied; access keys are not used anyway).
  statement {
    sid    = "NothingWithoutMfa"
    effect = "Deny"
    not_actions = [
      "iam:ChangePassword", "iam:GetUser", "iam:CreateVirtualMFADevice",
      "iam:EnableMFADevice", "iam:ListMFADevices", "iam:ResyncMFADevice",
      "sts:GetSessionToken", "signin:AuthorizeOAuth2Access", "signin:CreateOAuth2Token",
    ]
    resources = ["*"]
    condition {
      test     = "BoolIfExists"
      variable = "aws:MultiFactorAuthPresent"
      values   = ["false"]
    }
  }
}

resource "aws_iam_group_policy" "developers" {
  name   = "itr-developers"
  group  = aws_iam_group.developers.name
  policy = data.aws_iam_policy_document.developers.json
}
