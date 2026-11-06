# Bootstrap: applied by the lead, from a laptop

Creates what every other Terraform stack and CI depend on. This is the one stack that is
applied by hand (first time, and when it changes); everything else is applied by CI.

| What | Why | Cost |
| --- | --- | --- |
| S3 bucket `itr-tfstate-<account>` (versioning, encryption, native locking) | Terraform state for all stacks | Cents |
| GitHub OIDC provider, roles `itr-gha-plan` and `itr-gha-apply` | CI signs in without stored keys | Free |
| Policy `itr-guardrails` | Region, instance-size and cost limits without AWS Organizations | Free |
| Groups `admins`, `developers` | People | Free |
| Budget `itr-monthly`: alerts at $25 / $40 / $50 actual, $50 forecast | No surprises | Free (first 2 budgets) |
| Cost anomaly subscription (impact ≥ $5) | Catches a forgotten resource early | Free |
| SNS topic `itr-alerts`, email backup, Telegram relay Lambda | Alerts land in the Telegram Alerts topic | Free tier |

## Before you start

1. Signed in as your IAM admin user (`aws login`, `aws sts get-caller-identity`), not root.
2. The bot token is in SSM: `aws ssm get-parameter --name /itr/telegram/bot_token --query Parameter.Name`.
3. `cp terraform.tfvars.example terraform.tfvars` and fill it in.
4. Check for an existing GitHub OIDC provider: `aws iam list-open-id-connect-providers`.

## Apply

```bash
cd infra/bootstrap
terraform init
terraform plan -out=bootstrap.plan
terraform apply bootstrap.plan
```

## After the first apply

1. Confirm the SNS email subscription from your inbox.
2. Move the state into S3: uncomment the `backend "s3"` block in `versions.tf`, run
   `terraform init -migrate-state`, answer `yes`, delete the local `terraform.tfstate*` files.
3. GitHub → AEA-GO/incidenttrainer → Settings → Secrets and variables → Actions → **Variables**
   (ARNs are not secret): `AWS_PLAN_ROLE_ARN`, `AWS_APPLY_ROLE_ARN` from `terraform output ci_role_arns`.
4. GitHub → Settings → Environments → create `main`, allow only the `main` branch.
5. Add yourself to the `admins` group, then detach the AdministratorAccess policy from your user.
6. Test the alert path:
   `aws sns publish --topic-arn "$(terraform output -raw alerts_topic_arn)" --subject "Test" --message "hello from bootstrap"`
   should appear in the Telegram Alerts topic.
7. When a teammate gets an IAM user: add it to `developers` and test that `aws sts get-caller-identity`
   works after `aws login` (see the note on the MFA rule in `humans.tf`).
