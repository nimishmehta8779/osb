variable "name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "Subnets for the NLB. Use the private subnets."
  type        = list(string)
}

variable "instance_id" {
  description = "Envoy instance registered to the target group."
  type        = string
}

variable "private_zone_id" {
  description = "Private Route 53 zone ID."
  type        = string
}

variable "zone_name" {
  description = "Private zone name, for example osb.internal."
  type        = string
}

variable "record_name" {
  description = "Record label inside the private zone, for example edge."
  type        = string
  default     = "edge"
}

variable "tags" {
  type    = map(string)
  default = {}
}
