variable "name" {
  description = "Prefix for every resource, e.g. itr-main."
  type        = string
}

variable "table_name" {
  description = "DynamoDB table the API reads and writes."
  type        = string
}

variable "table_arn" {
  type = string
}

variable "sandbox_subnet_ids" {
  description = "Where the launcher may start game servers, first zone first (modules/network)."
  type        = list(string)
}

variable "launch_template_arns" {
  description = "The game servers' launch templates (modules/sandbox)."
  type        = list(string)
}
