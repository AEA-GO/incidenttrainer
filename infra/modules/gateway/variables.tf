variable "name" {
  description = "Prefix for every resource, e.g. itr-main."
  type        = string
}

variable "subnet_id" {
  description = "The private platform subnet."
  type        = string
}

variable "security_group_id" {
  description = "The gateway security group (modules/network)."
  type        = string
}

variable "table_name" {
  type = string
}

variable "table_arn" {
  type = string
}

variable "labs_bucket" {
  description = "Lab content bucket; the gateway reads labs/<lab>/v<version>/runbook.json."
  type        = string
}

variable "labs_bucket_arn" {
  type = string
}

variable "public_origin" {
  description = "The address players use, e.g. https://drer7le45ultl.cloudfront.net (agent claim + WebSocket Origin check)."
  type        = string
}

variable "instance_type" {
  description = "Decided 20 Nov: t4g.micro."
  type        = string
  default     = "t4g.micro"
}

variable "ami_parameter" {
  description = "SSM parameter with the gateway image id (written by the gateway-image workflow)."
  type        = string
  default     = "/itr/gateway/ami"
}
