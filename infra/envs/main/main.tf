# The one AWS environment of the MVP ("main"). Applied by CI after merge, never by hand.
# Planned next: website hosting (S3 + CloudFront), DynamoDB, the API Lambda, the gateway.

module "network" {
  source = "../../modules/network"
  name   = "itr-main"
  cidr   = "10.20.0.0/16"
}
