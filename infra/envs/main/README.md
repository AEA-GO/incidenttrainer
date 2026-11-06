# infra/envs/main

The MVP's one AWS environment. **CI applies it**; never run `terraform apply` here by hand.

- Pull request: CI runs `terraform plan` with the read-only role `itr-gha-plan` and posts the plan as a PR comment.
- Merge to `main`: CI runs `terraform apply` with `itr-gha-apply`, inside the GitHub environment `main`, then `deploy-web` uploads the website and `deploy-api` the API code.
- On a laptop you may run `terraform init` and `terraform plan` (signed in with `aws login`) to preview.

State: `s3://itr-tfstate-557541683274/envs/main/terraform.tfstate` (native locking).

| Part | Cost |
| --- | --- |
| Network: VPC, 2 private subnets, S3 and DynamoDB gateway endpoints, EC2 Instance Connect Endpoint, security groups | $0 |
| Website: private S3 bucket, CloudFront (OAC, SPA routing function, security headers) | ~$0 (CloudFront free tier) |
| Data: DynamoDB table `itr-main` (on-demand, TTL, point-in-time recovery, deletion protection) | cents |
| API: Lambda `itr-main-api` (FastAPI, arm64) behind an HTTP API, reached through CloudFront `/api/*` | cents |

Website address: `terraform output -raw website_url` (a `*.cloudfront.net` address until the domain is bought).
