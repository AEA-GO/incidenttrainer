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
