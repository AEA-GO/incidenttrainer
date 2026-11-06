# GitHub Actions signs in with OIDC; no AWS keys are stored in GitHub.
# itr-gha-plan : pull requests and pushes to main (read-only, plus the state lock file)
# itr-gha-apply: only jobs that run in the GitHub environment "main"
# A role for the labs repo's publish job is added later, when that job exists.

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.existing_github_oidc_provider_arn == "" ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  # GitHub's OIDC subject includes immutable ids: repo:OWNER@ID/REPO@ID:...
  repo              = "${var.github_org}@${var.github_org_id}/${var.github_repo}@${var.github_repo_id}"
  oidc_provider_arn = var.existing_github_oidc_provider_arn != "" ? var.existing_github_oidc_provider_arn : aws_iam_openid_connect_provider.github[0].arn

  ci_roles = {
    plan  = ["repo:${local.repo}:pull_request", "repo:${local.repo}:ref:refs/heads/main"]
    apply = ["repo:${local.repo}:environment:main"]
  }
}

data "aws_iam_policy_document" "ci_trust" {
  for_each = local.ci_roles

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
      values   = each.value
    }
  }
}

resource "aws_iam_role" "ci" {
  for_each             = local.ci_roles
  name                 = "itr-gha-${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.ci_trust[each.key].json
  max_session_duration = 3600
}

# --- plan: read everything, write only the lock file ---------------------------

resource "aws_iam_role_policy_attachment" "plan_readonly" {
  role       = aws_iam_role.ci["plan"].name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "state_lock" {
  statement {
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/*.tflock"]
  }
}

resource "aws_iam_role_policy" "plan_state" {
  name   = "itr-terraform-state-lock"
  role   = aws_iam_role.ci["plan"].id
  policy = data.aws_iam_policy_document.state_lock.json
}

# --- apply: PowerUser + IAM limited to itr-* + guardrails -----------------------

data "aws_iam_policy_document" "apply_iam" {
  statement {
    sid = "ManageOwnIam"
    actions = [
      "iam:*Role*", "iam:*Policy*", "iam:*InstanceProfile*", "iam:TagRole", "iam:UntagRole",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:role/itr-*",
      "arn:aws:iam::${local.account_id}:policy/itr-*",
      "arn:aws:iam::${local.account_id}:instance-profile/itr-*",
    ]
  }
  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "apply_iam" {
  name   = "itr-own-iam"
  role   = aws_iam_role.ci["apply"].id
  policy = data.aws_iam_policy_document.apply_iam.json
}

resource "aws_iam_role_policy_attachment" "apply_poweruser" {
  role       = aws_iam_role.ci["apply"].name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

resource "aws_iam_role_policy_attachment" "apply_guardrails" {
  role       = aws_iam_role.ci["apply"].name
  policy_arn = aws_iam_policy.guardrails.arn
}
