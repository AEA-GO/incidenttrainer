output "vpc_id" {
  value = aws_vpc.this.id
}

output "sandbox_subnet_id" {
  value = aws_subnet.sandboxes.id
}

output "sandbox_subnets" {
  description = "Every sandbox subnet, first zone first: [{id, az, cidr}]."
  value = concat(
    [{ id = aws_subnet.sandboxes.id, az = aws_subnet.sandboxes.availability_zone, cidr = aws_subnet.sandboxes.cidr_block }],
    [for az in var.more_sandbox_azs : {
      id   = aws_subnet.sandboxes_more[az].id
      az   = az
      cidr = aws_subnet.sandboxes_more[az].cidr_block
    }],
  )
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

output "internet_gateway_id" {
  description = "Attached with no routes; CloudFront VPC origins need it to exist first."
  value       = aws_internet_gateway.this.id
}
