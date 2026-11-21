# infra/envs/main

The MVP's one AWS environment. **CI applies it**; never run `terraform apply` here by hand.

- Pull request: CI runs `terraform plan` with the read-only role `itr-gha-plan` and posts the plan as a PR comment.
- Merge to `main`: CI runs `terraform apply` with `itr-gha-apply`, inside the GitHub environment `main`, then `deploy-web` uploads the website and `deploy-api` the API code.
- On a laptop you may run `terraform init` and `terraform plan` (signed in with `aws login`) to preview.

State: `s3://itr-tfstate-557541683274/envs/main/terraform.tfstate` (native locking).

| Part | Cost |
| --- | --- |
| Network: VPC, private subnets (sandboxes in zones a/b/c, platform in a), S3 and DynamoDB gateway endpoints, EC2 Instance Connect Endpoint, security groups, an internet gateway with **no routes** (CloudFront VPC origins require one) | $0 |
| Website: private S3 bucket, CloudFront (OAC, SPA routing function, security headers) | ~$0 (CloudFront free tier) |
| Data: DynamoDB table `itr-main` (on-demand, TTL, point-in-time recovery, deletion protection) | cents |
| API: Lambda `itr-main-api` (FastAPI, arm64) behind an HTTP API, reached through CloudFront `/api/*` | cents |
| Labs: private bucket `itr-main-labs-<account>` (`labs/<lab>/v<version>/{card,runbook,debrief}.json`), versioned, HTTPS only | cents |
| Sandbox: launch template `itr-main-teapot-basic` (image from SSM, no role, no user data, IMDSv2 hop 1, shutdown = terminate); the launcher adds the subnet and, if needed, a fallback type | $0 until a game runs (~3 cents a game) |
| Gateway: t4g.micro `itr-main-gateway` in the platform subnet from the gateway image (SSM `/itr/gateway/ami`), role for the game table + runbooks only, CloudFront `/s/*` through a VPC origin | ~$7.80/month |

Website address: `terraform output -raw website_url` (a `*.cloudfront.net` address until the domain is bought).

**The gateway's image** is not changed by Terraform after the server exists (`ami` is
ignored): `.github/workflows/gateway-image.yml` puts new images on it by swapping its root
disk (see `gateway/image/README.md`). Creating or changing the VPC origin takes up to 15
minutes, so the first apply with the gateway takes ~20-25 minutes.
