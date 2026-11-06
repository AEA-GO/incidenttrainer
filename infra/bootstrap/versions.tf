terraform {
  required_version = ">= 1.17"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # After the first apply: uncomment, run `terraform init -migrate-state`,
  # answer yes, then delete the local terraform.tfstate* files.
  backend "s3" {
    bucket       = "itr-tfstate-557541683274"
    key          = "bootstrap/terraform.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project = "incidenttrainer"
      env     = "shared"
      stack   = "bootstrap"
    }
  }
}
