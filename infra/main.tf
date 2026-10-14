terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project     = "osb"
      environment = "dev"
      managed_by  = "terraform"
    }
  }
}

provider "tls" {}

variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "name" {
  type    = string
  default = "osb-dev"
}

variable "vpc_cidr" {
  description = "Placeholder. Confirm before apply."
  type        = string
  default     = "10.20.0.0/16"
}

variable "zone_name" {
  description = "Private zone name. Never resolves publicly."
  type        = string
  default     = "osb.internal"
}

variable "envoy_image" {
  description = "Envoy image pinned by tag and digest."
  type        = string
  default     = "envoyproxy/envoy:v1.39.2@sha256:460f8c329f24b2e1c7c5af64cb314a6f351ad1c262ba77b135ac46b35ebd5f85"
}

variable "alert_email" {
  description = "Placeholder. Subscribed to the CloudWatch alarm SNS topic if set; left empty, alarms fire with no notification path."
  type        = string
  default     = ""
}

# ---- TLS: self-signed cert for the Envoy data-plane listener ----
resource "tls_private_key" "envoy" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "envoy" {
  private_key_pem = tls_private_key.envoy.private_key_pem

  subject {
    common_name  = var.zone_name
    organization = "osb-dev (self-signed, not for public trust)"
  }

  dns_names = [var.zone_name, "*.${var.zone_name}"]

  validity_period_hours = 24 * 365
  allowed_uses = [
    "key_encipherment",
    "digital_signature",
    "server_auth",
  ]
}

# ---- CloudWatch: log groups, alarms, SNS ----
resource "aws_cloudwatch_log_group" "envoy" {
  name              = "/osb/${var.name}/envoy"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "sovereign" {
  name              = "/osb/${var.name}/sovereign"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "broker" {
  name              = "/osb/${var.name}/broker"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/osb/${var.name}/worker"
  retention_in_days = 14
}

resource "aws_sns_topic" "alarms" {
  name = "${var.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Alarm 1: SQS DLQ has at least one message.
resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "${var.name}-dlq-depth"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "One or more provisioning messages failed repeatedly and moved to the DLQ. See docs/operations.md."
  alarm_actions       = [aws_sns_topic.alarms.arn]

  dimensions = {
    QueueName = "${var.name}-provision-dlq"
  }
}

# Alarm 2: NLB has unhealthy targets (plain HTTP listener target group).
resource "aws_cloudwatch_metric_alarm" "nlb_unhealthy_plain" {
  alarm_name          = "${var.name}-nlb-unhealthy-plain"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/NetworkELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "Envoy instance failing NLB health check on port 9901 (/ready). Plain target group."
  alarm_actions       = [aws_sns_topic.alarms.arn]

  dimensions = {
    TargetGroup  = module.edge.envoy_target_group_arn_suffix
    LoadBalancer = module.edge.nlb_arn_suffix
  }
}

# Alarm 3: NLB has unhealthy targets (TLS listener target group).
resource "aws_cloudwatch_metric_alarm" "nlb_unhealthy_tls" {
  alarm_name          = "${var.name}-nlb-unhealthy-tls"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/NetworkELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "Envoy instance failing NLB health check on port 9901 (/ready). TLS target group."
  alarm_actions       = [aws_sns_topic.alarms.arn]

  dimensions = {
    TargetGroup  = module.edge.envoy_tls_target_group_arn_suffix
    LoadBalancer = module.edge.nlb_arn_suffix
  }
}

# Alarm 4: EC2 status check failure on any of the 4 instances.
locals {
  monitored_instances = {
    envoy     = module.envoy.instance_id
    sovereign = module.control_plane.instance_id
    broker    = module.broker.instance_id
    worker    = module.worker.instance_id
  }
}

resource "aws_cloudwatch_metric_alarm" "instance_status" {
  for_each = local.monitored_instances

  alarm_name          = "${var.name}-${each.key}-status-check"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "StatusCheckFailed"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "EC2 system or instance status check failed for ${each.key} (${each.value})."
  alarm_actions       = [aws_sns_topic.alarms.arn]

  dimensions = {
    InstanceId = each.value
  }
}

locals {
  lkg_dir = "/var/lib/osb/lkg"

  # Envoy bootstrap with the LKG path filled in. Terraform escapes ${...} as $${...}.
  envoy_config = replace(
    file("${path.module}/../envoy/bootstrap/envoy-lkg.yaml"),
    "$${LKG_DIR}",
    local.lkg_dir
  )

  # Broker and worker both import services.common, so their Docker build
  # context is "the repo root". Each gets zipped and uploaded to S3; the boot script
  # downloads and unzips it before building.
  common_dir     = "${path.module}/../services/common"
  broker_app_dir = "${path.module}/../services/broker/app"
  worker_app_dir = "${path.module}/../services/worker/app"

  common_files     = fileset(local.common_dir, "*.py")
  broker_app_files = fileset(local.broker_app_dir, "*.py")
  worker_app_files = fileset(local.worker_app_dir, "*.py")
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "artifacts" {
  bucket        = "${var.name}-artifacts-${data.aws_caller_identity.current.account_id}"
  force_destroy = true # deploy artifacts only; nothing here needs to survive a destroy

  tags = { Name = "${var.name}-artifacts" }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "archive_file" "broker" {
  type        = "zip"
  output_path = "${path.module}/.build/broker.zip"

  dynamic "source" {
    for_each = merge(
      { for f in local.common_files : "services/common/${f}" => "${local.common_dir}/${f}" },
      { for f in local.broker_app_files : "services/broker/app/${f}" => "${local.broker_app_dir}/${f}" },
      {
        "services/broker/requirements.txt" = "${path.module}/../services/broker/requirements.txt"
        "services/broker/Dockerfile"       = "${path.module}/../services/broker/Dockerfile"
      }
    )
    content {
      filename = source.key
      content  = file(source.value)
    }
  }
}

data "archive_file" "worker" {
  type        = "zip"
  output_path = "${path.module}/.build/worker.zip"

  dynamic "source" {
    for_each = merge(
      { for f in local.common_files : "services/common/${f}" => "${local.common_dir}/${f}" },
      { for f in local.worker_app_files : "services/worker/app/${f}" => "${local.worker_app_dir}/${f}" },
      {
        "services/worker/requirements.txt" = "${path.module}/../services/worker/requirements.txt"
        "services/worker/Dockerfile"       = "${path.module}/../services/worker/Dockerfile"
      }
    )
    content {
      filename = source.key
      content  = file(source.value)
    }
  }
}

resource "aws_s3_object" "broker_artifact" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "broker.zip"
  source = data.archive_file.broker.output_path
  etag   = data.archive_file.broker.output_md5
}

resource "aws_s3_object" "worker_artifact" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "worker.zip"
  source = data.archive_file.worker.output_path
  etag   = data.archive_file.worker.output_md5
}

resource "aws_s3_object" "agent_script" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "agent.py"
  source = "${path.module}/../envoy/lkg-agent/agent.py"
  etag   = filemd5("${path.module}/../envoy/lkg-agent/agent.py")
}

module "network" {
  source = "./modules/network"

  name     = var.name
  region   = var.region
  vpc_cidr = var.vpc_cidr
  az_count = 3
}

module "private_zone" {
  source = "./modules/private-zone"

  name      = var.name
  zone_name = var.zone_name
  vpc_id    = module.network.vpc_id
}

module "control_plane" {
  source = "./modules/control-plane"

  name            = var.name
  vpc_id          = module.network.vpc_id
  vpc_cidr        = module.network.vpc_cidr
  subnet_id       = module.network.private_subnet_ids[1]
  private_zone_id = module.private_zone.zone_id
  zone_name       = var.zone_name

  user_data = templatefile("${path.module}/modules/control-plane/user_data.sh.tftpl", {
    dockerfile_b64 = filebase64("${path.module}/../services/sovereign/Dockerfile")
    config_b64     = filebase64("${path.module}/../services/sovereign/config/sovereign.yaml")
    listeners_b64  = filebase64("${path.module}/../services/sovereign/templates/1.39.2/listeners.yaml.j2")
    clusters_b64   = filebase64("${path.module}/../services/sovereign/templates/1.39.2/clusters.yaml.j2")
    log_group      = aws_cloudwatch_log_group.sovereign.name
    region         = var.region
  })
}

module "envoy" {
  source = "./modules/envoy-instance"

  name                   = var.name
  vpc_id                 = module.network.vpc_id
  vpc_cidr               = module.network.vpc_cidr
  subnet_id              = module.network.private_subnet_ids[0]
  artifact_s3_bucket_arn = aws_s3_bucket.artifacts.arn

  user_data = templatefile("${path.module}/../envoy/install/user_data.sh.tftpl", {
    envoy_image        = var.envoy_image
    envoy_config       = local.envoy_config
    agent_s3_bucket    = aws_s3_bucket.artifacts.id
    agent_s3_key       = aws_s3_object.agent_script.key
    sovereign_endpoint = "sovereign.${var.zone_name}"
    lkg_dir            = local.lkg_dir
    tls_cert_b64       = base64encode(tls_self_signed_cert.envoy.cert_pem)
    tls_key_b64        = base64encode(tls_private_key.envoy.private_key_pem)
    log_group          = aws_cloudwatch_log_group.envoy.name
    region             = var.region
  })
}

module "provisioning" {
  source = "./modules/provisioning"

  name             = var.name
  private_zone_arn = "arn:aws:route53:::hostedzone/${module.private_zone.zone_id}"
}

module "edge" {
  source = "./modules/edge-region"

  name            = var.name
  vpc_id          = module.network.vpc_id
  subnet_ids      = module.network.private_subnet_ids
  instance_id     = module.envoy.instance_id
  private_zone_id = module.private_zone.zone_id
  zone_name       = var.zone_name
}

module "broker" {
  source = "./modules/app-instance"

  name      = "${var.name}-broker"
  vpc_id    = module.network.vpc_id
  vpc_cidr  = module.network.vpc_cidr
  subnet_id = module.network.private_subnet_ids[1]

  image_name             = "osb/broker:dev"
  dockerfile             = "services/broker/Dockerfile"
  artifact_s3_bucket     = aws_s3_bucket.artifacts.id
  artifact_s3_key        = aws_s3_object.broker_artifact.key
  artifact_s3_bucket_arn = aws_s3_bucket.artifacts.arn
  container_ports        = [{ host = 8000, container = 8000 }]
  iam_policy_arns        = [module.provisioning.broker_policy_arn]
  log_group              = aws_cloudwatch_log_group.broker.name
  region                 = var.region

  container_env = {
    AWS_REGION          = var.region
    TENANTS_TABLE       = module.provisioning.tenants_table
    HOSTNAMES_TABLE     = module.provisioning.hostnames_table
    API_KEYS_TABLE      = module.provisioning.api_keys_table
    PROVISION_QUEUE_URL = module.provisioning.provision_queue_url
    ZONE_SUFFIX         = var.zone_name
  }
}

module "worker" {
  source = "./modules/app-instance"

  name      = "${var.name}-worker"
  vpc_id    = module.network.vpc_id
  vpc_cidr  = module.network.vpc_cidr
  subnet_id = module.network.private_subnet_ids[2]

  image_name             = "osb/worker:dev"
  dockerfile             = "services/worker/Dockerfile"
  artifact_s3_bucket     = aws_s3_bucket.artifacts.id
  artifact_s3_key        = aws_s3_object.worker_artifact.key
  artifact_s3_bucket_arn = aws_s3_bucket.artifacts.arn
  container_ports        = [] # nothing calls the worker; it only consumes the queue
  iam_policy_arns        = [module.provisioning.worker_policy_arn]
  log_group              = aws_cloudwatch_log_group.worker.name
  region                 = var.region

  container_env = {
    AWS_REGION          = var.region
    TENANTS_TABLE       = module.provisioning.tenants_table
    HOSTNAMES_TABLE     = module.provisioning.hostnames_table
    PROVISION_QUEUE_URL = module.provisioning.provision_queue_url
    PRIVATE_ZONE_ID     = module.private_zone.zone_id
    NLB_DNS_NAME        = module.edge.nlb_dns_name
    NLB_ZONE_ID         = module.edge.nlb_zone_id
    # DNS_ADAPTER unset: entry.py defaults to the real Route 53 adapter.
  }
}

# Private DNS name so Sovereign's hardcoded context URL (services/sovereign/config/sovereign.yaml)
# resolves. Plain A record, not an alias: the broker is one instance, not a load balancer.
resource "aws_route53_record" "broker" {
  zone_id = module.private_zone.zone_id
  name    = "broker.${var.zone_name}"
  type    = "A"
  ttl     = 60
  records = [module.broker.private_ip]
}

output "vpc_id" {
  value = module.network.vpc_id
}

output "envoy_private_ip" {
  value = module.envoy.private_ip
}

output "sovereign_private_ip" {
  value = module.control_plane.private_ip
}

output "edge_endpoint" {
  value = module.edge.endpoint
}

output "provision_queue_url" {
  value = module.provisioning.provision_queue_url
}

output "nlb_dns_name" {
  value = module.edge.nlb_dns_name
}

output "broker_private_ip" {
  value = module.broker.private_ip
}

output "worker_private_ip" {
  value = module.worker.private_ip
}

output "broker_endpoint" {
  value = "broker.${var.zone_name}:8000"
}

output "api_keys_table" {
  value = module.provisioning.api_keys_table
}
