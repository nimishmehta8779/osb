variable "name" {
  description = "Name prefix for network resources."
  type        = string
}

variable "region" {
  description = "AWS region. Used to build VPC endpoint service names."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR block. Must not overlap with other VPCs or on-prem ranges you route to."
  type        = string
}

variable "az_count" {
  description = "Number of AZs to use. The architecture requires 3."
  type        = number
  default     = 3

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3."
  }
}

variable "tags" {
  description = "Tags applied to all network resources."
  type        = map(string)
  default     = {}
}
