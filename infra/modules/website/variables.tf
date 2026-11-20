variable "name" {
  description = "Prefix for every resource, e.g. itr-main."
  type        = string
}

variable "api_origin_domain" {
  description = "Hostname of the session API (API Gateway) that serves /api/*."
  type        = string
}

variable "gateway_instance_arn" {
  description = "The gateway EC2 instance (modules/gateway), CloudFront's VPC origin for /s/*."
  type        = string
}

variable "gateway_private_dns" {
  description = "The gateway's private DNS name: the origin domain of a VPC origin on EC2."
  type        = string
}

variable "internet_gateway_id" {
  description = "The VPC's internet gateway: a VPC origin can only be created once it exists."
  type        = string
}
