terraform {
  required_version = ">= 1.17"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket       = "itr-tfstate-557541683274"
    key          = "envs/main/terraform.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = "eu-central-1"

  default_tags {
    tags = {
      project = "incidenttrainer"
      env     = "main"
      stack   = "envs/main"
    }
  }
}
