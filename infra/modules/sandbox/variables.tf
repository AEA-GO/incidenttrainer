variable "name" {
  description = "Prefix for every resource, e.g. itr-main."
  type        = string
}

variable "worlds" {
  description = "One launch template per world image (SSM /itr/worlds/<world>/ami)."
  type        = list(string)
  default     = ["teapot-basic"]
}

variable "security_group_id" {
  description = "The sandbox security group (modules/network): only the gateway and EICE may connect."
  type        = string
}
