# IncidentTrainer network: two private subnets, no internet gateway, no NAT.
#
#   sandboxes subnet - one EC2 per game. Its route table has only the local route and
#                      no endpoints: game servers cannot reach anything outside the VPC.
#   platform subnet  - the gateway EC2 (a CloudFront VPC origin) and the EC2 Instance
#                      Connect Endpoint. Reaches S3 and DynamoDB through free gateway
#                      endpoints only.
#
# The gateway is the only thing that talks to a game server's agent (player API 8080,
# control API 8081), so there is no VPC Lambda.
#
# Cost: $0. VPC, subnets, gateway endpoints and the Instance Connect Endpoint are free;
# there are no NAT gateways and no public IPv4 addresses.
# Packer builds run in the account's default VPC, which has internet access.

data "aws_region" "current" {}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.name }
}

# The VPC's default security group allows everything between its members and all
# outbound traffic. Taking it over with no rules makes it useless, so nothing can
# end up in it by accident.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-default-unused" }
}

# --- Subnets and route tables ------------------------------------------------

resource "aws_subnet" "sandboxes" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.cidr, 4, 0) # 10.20.0.0/20, 4091 usable addresses
  availability_zone       = var.az
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.name}-sandboxes", tier = "sandbox" }
}

resource "aws_subnet" "platform" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.cidr, 8, 16) # 10.20.16.0/24
  availability_zone       = var.az
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.name}-platform", tier = "platform" }
}

resource "aws_route_table" "sandboxes" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-sandboxes" } # local route only, on purpose
}

resource "aws_route_table" "platform" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-platform" }
}

resource "aws_route_table_association" "sandboxes" {
  subnet_id      = aws_subnet.sandboxes.id
  route_table_id = aws_route_table.sandboxes.id
}

resource "aws_route_table_association" "platform" {
  subnet_id      = aws_subnet.platform.id
  route_table_id = aws_route_table.platform.id
}

# --- Free gateway endpoints, platform subnet only -----------------------------

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.platform.id]
  tags              = { Name = "${var.name}-s3" }
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.platform.id]
  tags              = { Name = "${var.name}-dynamodb" }
}

# --- Security groups (rules below) --------------------------------------------
# Terraform removes AWS's default allow-all egress rule from groups it creates,
# so a group without egress rules can send nothing.

resource "aws_security_group" "sandbox" {
  name        = "${var.name}-sandbox"
  description = "Game servers: inbound from the gateway and EICE only; no outbound"
  vpc_id      = aws_vpc.this.id
}

resource "aws_security_group" "gateway" {
  name        = "${var.name}-gateway"
  description = "Gateway EC2: inbound from CloudFront only"
  vpc_id      = aws_vpc.this.id
}

resource "aws_security_group" "eice" {
  name        = "${var.name}-eice"
  description = "EC2 Instance Connect Endpoint for team SSH"
  vpc_id      = aws_vpc.this.id
}

locals {
  # port => description
  sandbox_ports_from_gateway = {
    8080 = "agent player API"
    8081 = "agent control API (runbook in, report out)"
    7681 = "ttyd terminal"
    3000 = "Grafana"
  }
}

# Sandbox inbound
resource "aws_vpc_security_group_ingress_rule" "sandbox_from_gateway" {
  for_each                     = local.sandbox_ports_from_gateway
  security_group_id            = aws_security_group.sandbox.id
  referenced_security_group_id = aws_security_group.gateway.id
  ip_protocol                  = "tcp"
  from_port                    = each.key
  to_port                      = each.key
  description                  = each.value
}

resource "aws_vpc_security_group_ingress_rule" "sandbox_from_eice" {
  security_group_id            = aws_security_group.sandbox.id
  referenced_security_group_id = aws_security_group.eice.id
  ip_protocol                  = "tcp"
  from_port                    = 22
  to_port                      = 22
  description                  = "team SSH through EC2 Instance Connect Endpoint"
}

# Gateway inbound and outbound
resource "aws_vpc_security_group_ingress_rule" "gateway_from_cloudfront" {
  security_group_id = aws_security_group.gateway.id
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  description       = "CloudFront VPC origin"
}

resource "aws_vpc_security_group_ingress_rule" "gateway_from_eice" {
  security_group_id            = aws_security_group.gateway.id
  referenced_security_group_id = aws_security_group.eice.id
  ip_protocol                  = "tcp"
  from_port                    = 22
  to_port                      = 22
  description                  = "team SSH"
}

resource "aws_vpc_security_group_egress_rule" "gateway_to_sandbox" {
  for_each                     = local.sandbox_ports_from_gateway
  security_group_id            = aws_security_group.gateway.id
  referenced_security_group_id = aws_security_group.sandbox.id
  ip_protocol                  = "tcp"
  from_port                    = each.key
  to_port                      = each.key
  description                  = each.value
}

resource "aws_vpc_security_group_egress_rule" "gateway_to_aws" {
  for_each = {
    s3       = aws_vpc_endpoint.s3.prefix_list_id
    dynamodb = aws_vpc_endpoint.dynamodb.prefix_list_id
  }
  security_group_id = aws_security_group.gateway.id
  prefix_list_id    = each.value
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "${each.key} through the gateway endpoint"
}

# EICE outbound
resource "aws_vpc_security_group_egress_rule" "eice_ssh" {
  for_each = {
    sandbox = aws_security_group.sandbox.id
    gateway = aws_security_group.gateway.id
  }
  security_group_id            = aws_security_group.eice.id
  referenced_security_group_id = each.value
  ip_protocol                  = "tcp"
  from_port                    = 22
  to_port                      = 22
  description                  = "SSH to ${each.key}"
}

# --- EC2 Instance Connect Endpoint (free) ---------------------------------------

resource "aws_ec2_instance_connect_endpoint" "this" {
  subnet_id          = aws_subnet.platform.id
  security_group_ids = [aws_security_group.eice.id]
  preserve_client_ip = false
  tags               = { Name = "${var.name}-eice" }
}
