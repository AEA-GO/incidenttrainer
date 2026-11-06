variable "name" {
  description = "Prefix for every resource, e.g. itr-main."
  type        = string
}

variable "cidr" {
  description = "VPC range. Sandboxes get the first /20, the platform subnet the next /24."
  type        = string
  default     = "10.20.0.0/16"
}

variable "az" {
  description = "One Availability Zone keeps cross-AZ data charges at zero for the MVP."
  type        = string
  default     = "eu-central-1a"
}
