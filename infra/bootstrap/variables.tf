variable "region" {
  description = "The only region IncidentTrainer runs in."
  type        = string
  default     = "eu-central-1"
}

variable "github_org" {
  description = "GitHub organization (case must match GitHub exactly)."
  type        = string
  default     = "AEA-GO"
}

variable "github_repo" {
  description = "Platform repository whose CI may sign in to AWS."
  type        = string
  default     = "incidenttrainer"
}

variable "alert_email" {
  description = "Backup address for budget alerts (Telegram is the main channel). Kept out of Git in terraform.tfvars."
  type        = string
}

variable "budget_alerts_usd" {
  description = "Monthly budget alerts in USD (actual spend). The highest value is the budget."
  type        = list(number)
  default     = [25, 40, 50]
}

variable "telegram_token_parameter" {
  description = "SSM SecureString with the alert bot token. Created by hand; never in Git or state."
  type        = string
  default     = "/itr/telegram/bot_token"
}

variable "telegram_chat_id" {
  description = "Telegram group id (starts with -100)."
  type        = string
}

variable "telegram_thread_id" {
  description = "message_thread_id of the Alerts topic."
  type        = string
}

variable "existing_anomaly_monitor_arn" {
  description = "Use an existing Cost Anomaly Detection monitor instead of creating one (an account allows one SERVICE monitor)."
  type        = string
  default     = ""
}

variable "existing_github_oidc_provider_arn" {
  description = "Use an existing GitHub OIDC provider instead of creating one (an account can have only one per URL)."
  type        = string
  default     = ""
}
