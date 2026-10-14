variable "name" {
  type = string
}

variable "private_zone_arn" {
  description = "ARN of the private hosted zone the worker manages records in."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
