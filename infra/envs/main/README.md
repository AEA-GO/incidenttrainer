# infra/envs/main

The MVP's one AWS environment. **CI applies it**; never run `terraform apply` here by hand.

- Pull request: CI runs `terraform plan` with the read-only role `itr-gha-plan` and posts the plan as a PR comment.
- Merge to `main`: CI runs `terraform apply` with `itr-gha-apply`, inside the GitHub environment `main`.
- On a laptop you may run `terraform init` and `terraform plan` (signed in with `aws login`) to preview.

State: `s3://itr-tfstate-557541683274/envs/main/terraform.tfstate` (native locking).

| Part | Cost |
| --- | --- |
| Network: VPC, 2 private subnets, S3 and DynamoDB gateway endpoints, EC2 Instance Connect Endpoint, security groups | $0 |
