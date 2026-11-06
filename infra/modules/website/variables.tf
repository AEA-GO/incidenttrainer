variable "name" {
  description = "Prefix for every resource, e.g. itr-main."
  type        = string
}

variable "api_origin_domain" {
  description = "Hostname of the session API (API Gateway) that serves /api/*."
  type        = string
}
