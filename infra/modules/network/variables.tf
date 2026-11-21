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
  description = "The platform subnet's zone and the first sandbox subnet's."
  type        = string
  default     = "eu-central-1a"
}

variable "more_sandbox_azs" {
  description = "More zones for sandbox subnets (capacity fallback). Ranges 10.20.32.0/20, 10.20.48.0/20, ..."
  type        = list(string)
  default     = ["eu-central-1b", "eu-central-1c"]
}
