# The one AWS environment of the MVP ("main"). Applied by CI after merge, never by hand.
# Planned next: DynamoDB, the API Lambda behind /api/*, the gateway behind /s/*.

module "network" {
  source = "../../modules/network"
  name   = "itr-main"
  cidr   = "10.20.0.0/16"
}

module "website" {
  source = "../../modules/website"
  name   = "itr-main"
}
