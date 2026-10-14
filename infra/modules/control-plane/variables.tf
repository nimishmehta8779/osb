variable "name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "subnet_id" {
  description = "Private subnet for the Sovereign host."
  type        = string
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "user_data" {
  description = "Rendered boot script that builds and runs the Sovereign container."
  type        = string
}

variable "private_zone_id" {
  type = string
}

variable "zone_name" {
  type = string
}

variable "record_name" {
  type    = string
  default = "sovereign"
}

variable "tags" {
  type    = map(string)
  default = {}
}
