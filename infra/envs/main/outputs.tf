output "network" {
  value = {
    vpc_id                       = module.network.vpc_id
    sandbox_subnet_id            = module.network.sandbox_subnet_id
    platform_subnet_id           = module.network.platform_subnet_id
    security_group_ids           = module.network.security_group_ids
    instance_connect_endpoint_id = module.network.instance_connect_endpoint_id
  }
}

# Read by the deploy-web CI job.
output "website_bucket" {
  value = module.website.bucket
}

output "website_distribution_id" {
  value = module.website.distribution_id
}

output "website_url" {
  value = module.website.url
}
