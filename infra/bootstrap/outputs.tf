output "state_bucket" {
  description = "Terraform state bucket for every other stack (infra/envs/main)."
  value       = aws_s3_bucket.tfstate.bucket
}

output "ci_role_arns" {
  description = "Store as GitHub Actions variables AWS_PLAN_ROLE_ARN and AWS_APPLY_ROLE_ARN."
  value       = { for name, role in aws_iam_role.ci : name => role.arn }
}

output "alerts_topic_arn" {
  description = "Other stacks send CloudWatch alarms here."
  value       = aws_sns_topic.alerts.arn
}

output "guardrails_policy_arn" {
  value = aws_iam_policy.guardrails.arn
}
