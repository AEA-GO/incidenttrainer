# DNS Firewall on the game VPC: Amazon's resolver answers only AWS and internal names.
#
# Security groups don't filter traffic to the VPC's own DNS resolver, so a player with a
# root shell on a game server could still smuggle data out inside DNS queries for a
# domain they control ("DNS tunnelling"), even though the server has no other way out.
# This "walled garden" allows the names our servers need and answers NXDOMAIN to the rest:
#   - *.amazonaws.com: the gateway reaches DynamoDB and S3 by name (through the VPC
#     endpoints). Queries for these names only ever reach AWS's own DNS servers.
#   - *.internal: EC2's own host names (ip-10-20-....compute.internal).
#   - *.10.in-addr.arpa: reverse lookups of our private addresses.
# Game servers themselves need no DNS outside the VPC (Docker resolves service names
# itself). Packer builds run in the default VPC and are not affected.
#
# Fails closed: if DNS Firewall can't answer, queries are blocked, not let through.
# Cost: $0.60 per million queries checked (a game makes a few hundred) plus a fraction of
# a cent per domain name a month, so cents a month at most.

resource "aws_route53_resolver_firewall_domain_list" "allowed" {
  name    = "${var.name}-allowed"
  domains = ["amazonaws.com.", "*.amazonaws.com.", "*.internal.", "*.10.in-addr.arpa."]
}

resource "aws_route53_resolver_firewall_domain_list" "everything" {
  name    = "${var.name}-everything"
  domains = ["*"]
}

resource "aws_route53_resolver_firewall_rule_group" "this" {
  name = "${var.name}-walled-garden"
}

# Rules run from the lowest priority number up: allow ours first, then block the rest.
resource "aws_route53_resolver_firewall_rule" "allow_ours" {
  name                    = "allow-aws-and-internal"
  action                  = "ALLOW"
  firewall_rule_group_id  = aws_route53_resolver_firewall_rule_group.this.id
  firewall_domain_list_id = aws_route53_resolver_firewall_domain_list.allowed.id
  priority                = 100
  # AWS endpoint names are CNAMEs to other amazonaws.com names; trust that chain.
  firewall_domain_redirection_action = "TRUST_REDIRECTION_DOMAIN"
}

resource "aws_route53_resolver_firewall_rule" "block_the_rest" {
  name                    = "block-everything-else"
  action                  = "BLOCK"
  block_response          = "NXDOMAIN"
  firewall_rule_group_id  = aws_route53_resolver_firewall_rule_group.this.id
  firewall_domain_list_id = aws_route53_resolver_firewall_domain_list.everything.id
  priority                = 200
}

resource "aws_route53_resolver_firewall_rule_group_association" "this" {
  name                   = "${var.name}-walled-garden"
  firewall_rule_group_id = aws_route53_resolver_firewall_rule_group.this.id
  vpc_id                 = aws_vpc.this.id
  priority               = 101 # the lowest allowed: before any other rule group
  mutation_protection    = "DISABLED"

  # Both rules exist before the group starts filtering the VPC.
  depends_on = [
    aws_route53_resolver_firewall_rule.allow_ours,
    aws_route53_resolver_firewall_rule.block_the_rest,
  ]
}

resource "aws_route53_resolver_firewall_config" "this" {
  resource_id        = aws_vpc.this.id
  firewall_fail_open = "DISABLED"
}
