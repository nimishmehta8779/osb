variable "name" {
  description = "Name for the instance, security group, and IAM role."
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "subnet_id" {
  description = "Private subnet. No public IP is assigned."
  type        = string
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "image_name" {
  description = "Local Docker tag built at boot, for example osb/broker:dev."
  type        = string
}

variable "dockerfile" {
  description = "Dockerfile path relative to the build context (the root of `files`)."
  type        = string
  default     = "Dockerfile"
}

variable "artifact_s3_bucket" {
  description = "S3 bucket holding this app's build-context zip (transferred via S3 to avoid EC2's 16KB user_data limit)."
  type        = string
}

variable "artifact_s3_key" {
  description = "Key of the build-context zip within artifact_s3_bucket."
  type        = string
}

variable "artifact_s3_bucket_arn" {
  description = "ARN of artifact_s3_bucket, for scoping the instance role's s3:GetObject to it."
  type        = string
}

variable "container_env" {
  description = "Environment variables passed to `docker run -e`."
  type        = map(string)
  default     = {}
}

variable "container_ports" {
  description = "Ports to publish and to open in the security group (host == container)."
  type = list(object({
    host      = number
    container = number
  }))
  default = []
}

variable "iam_policy_arns" {
  description = "Additional least-privilege policies beyond SSM."
  type        = list(string)
  default     = []
}

variable "log_group" {
  description = "CloudWatch log group this instance's container logs ship to."
  type        = string
}

variable "region" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
