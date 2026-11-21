# The one AWS environment of the MVP ("main"). Applied by CI after merge, never by hand.
# Planned next: the API's launcher permissions, the cleanup Lambda.

locals {
  # The address players use. Written as text, not read from the website module, because
  # the gateway needs it and CloudFront needs the gateway (a cycle). Changes with the domain.
  public_origin = "https://drer7le45ultl.cloudfront.net"
}

module "network" {
  source = "../../modules/network"
  name   = "itr-main"
  cidr   = "10.20.0.0/16"
}

module "data" {
  source = "../../modules/data"
  name   = "itr-main"
}

module "api" {
  source     = "../../modules/api"
  name       = "itr-main"
  table_name = module.data.table_name
  table_arn  = module.data.table_arn
}

module "labs" {
  source = "../../modules/labs"
  name   = "itr-main"
}

module "gateway" {
  source            = "../../modules/gateway"
  name              = "itr-main"
  subnet_id         = module.network.platform_subnet_id
  security_group_id = module.network.security_group_ids.gateway
  table_name        = module.data.table_name
  table_arn         = module.data.table_arn
  labs_bucket       = module.labs.bucket
  labs_bucket_arn   = module.labs.bucket_arn
  public_origin     = local.public_origin
}

module "sandbox" {
  source            = "../../modules/sandbox"
  name              = "itr-main"
  security_group_id = module.network.security_group_ids.sandbox
}

module "website" {
  source               = "../../modules/website"
  name                 = "itr-main"
  api_origin_domain    = module.api.origin_domain
  gateway_instance_arn = module.gateway.instance_arn
  gateway_private_dns  = module.gateway.private_dns
  internet_gateway_id  = module.network.internet_gateway_id
}
