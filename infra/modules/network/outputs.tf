output "vpc_id" {
  value = aws_vpc.this.id
}

output "sandbox_subnet_id" {
  value = aws_subnet.sandboxes.id
}

output "platform_subnet_id" {
  value = aws_subnet.platform.id
}

output "security_group_ids" {
  value = {
    sandbox = aws_security_group.sandbox.id
    gateway = aws_security_group.gateway.id
    eice    = aws_security_group.eice.id
  }
}

output "instance_connect_endpoint_id" {
  value = aws_ec2_instance_connect_endpoint.this.id
}
