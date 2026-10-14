variable "name" {
  description = "Name prefix for DNS resources."
  type        = string
}

variable "zone_name" {
  description = "Private zone name, for example osb.internal. Never a public domain you own unless intended."
  type        = string
}

variable "vpc_id" {
  description = "VPC the private zone is associated with."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
