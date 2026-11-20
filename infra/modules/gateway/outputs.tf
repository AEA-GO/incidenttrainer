output "instance_id" {
  value = aws_instance.gateway.id
}

output "instance_arn" {
  value = aws_instance.gateway.arn
}

output "private_dns" {
  description = "CloudFront's origin domain for the VPC origin."
  value       = aws_instance.gateway.private_dns
}
