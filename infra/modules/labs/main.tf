# Lab content bucket (private): what the platform reads about each lab, written only by
# the labs repo's CI (later) or by hand for now.
#
#   labs/<lab>/v<version>/card.json      the public card + scoring   (API)
#   labs/<lab>/v<version>/runbook.json   what the agent gets         (gateway, at boot)
#   labs/<lab>/v<version>/debrief.json   after the game ends         (API)
#   labs/<lab>/current                   which version is live       (API)
#
# Never public: no website hosting, no public access, readers are IAM roles only.
# Versioned, so a bad upload can be rolled back. Cost: cents (a few KB per lab).

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "labs" {
  bucket = "${var.name}-labs-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "labs" {
  bucket                  = aws_s3_bucket.labs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "labs" {
  bucket = aws_s3_bucket.labs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "labs" {
  bucket = aws_s3_bucket.labs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "labs" {
  bucket = aws_s3_bucket.labs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "labs" {
  bucket     = aws_s3_bucket.labs.id
  depends_on = [aws_s3_bucket_versioning.labs]

  rule {
    id     = "forget-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# Readers come through the network: refuse anything that isn't HTTPS.
data "aws_iam_policy_document" "labs" {
  statement {
    sid       = "HttpsOnly"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.labs.arn, "${aws_s3_bucket.labs.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "labs" {
  bucket     = aws_s3_bucket.labs.id
  policy     = data.aws_iam_policy_document.labs.json
  depends_on = [aws_s3_bucket_public_access_block.labs]
}
