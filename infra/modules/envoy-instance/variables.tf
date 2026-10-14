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
  description = "Private subnet for the instance. No public IP is assigned."
  type        = string
}

variable "instance_type" {
  description = "Dev: t3.small. Burstable, so load-test results will not reflect production sizing."
  type        = string
  default     = "t3.small"
}

variable "user_data" {
  description = "Rendered boot script that installs and starts Envoy."
  type        = string
}

variable "artifact_s3_bucket_arn" {
  description = "ARN of the S3 bucket the boot script downloads agent.py from (transferred via S3 to stay within EC2's 16KB user_data limit)."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
