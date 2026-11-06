# The one AWS environment of the MVP ("main"). Applied by CI after merge, never by hand.
# Planned next: the gateway behind /s/*, the server launcher, the cleanup Lambda.

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

module "website" {
  source            = "../../modules/website"
  name              = "itr-main"
  api_origin_domain = module.api.origin_domain
}
