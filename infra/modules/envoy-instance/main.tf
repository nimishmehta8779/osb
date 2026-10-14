# One Envoy host. Installed at boot from user_data (no AMI bake).
# No SSH: access is through SSM Session Manager.

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_security_group" "envoy" {
  name        = "${var.name}-envoy"
  description = "Envoy instances: traffic from the NLB inside the VPC only"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-envoy" })
}

# Listener traffic from the NLB (NLB sits in the VPC, so VPC CIDR covers it).
resource "aws_vpc_security_group_ingress_rule" "listener" {
  security_group_id = aws_security_group.envoy.id
  cidr_ipv4         = var.vpc_cidr
  from_port         = 10000
  to_port           = 10000
  ip_protocol       = "tcp"
  description       = "Envoy listener from VPC (NLB)"
}

# TLS listener (self-signed cert, Stage D+e). Same VPC-only scope as the plain
# listener above -- this terminates TLS at Envoy so clients get HTTPS inside
# the VPC, but it's self-signed, not a real CA cert (see docs/architecture.md).
resource "aws_vpc_security_group_ingress_rule" "listener_tls" {
  security_group_id = aws_security_group.envoy.id
  cidr_ipv4         = var.vpc_cidr
  from_port         = 10443
  to_port           = 10443
  ip_protocol       = "tcp"
  description       = "Envoy TLS listener from VPC (NLB)"
}

# Admin port: health checks from the NLB and in-VPC diagnostics only.
resource "aws_vpc_security_group_ingress_rule" "admin" {
  security_group_id = aws_security_group.envoy.id
  cidr_ipv4         = var.vpc_cidr
  from_port         = 9901
  to_port           = 9901
  ip_protocol       = "tcp"
  description       = "Envoy admin /ready from VPC (NLB health check)"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.envoy.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "Outbound via NAT (package install, image pull)"
}

resource "aws_iam_role" "envoy" {
  name = "${var.name}-envoy"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

# SSM agent access (Session Manager) so the smoke test runs without SSH.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.envoy.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  role       = aws_iam_role.envoy.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

data "aws_iam_policy_document" "artifact_read" {
  statement {
    sid       = "ReadAgentScript"
    actions   = ["s3:GetObject"]
    resources = ["${var.artifact_s3_bucket_arn}/*"]
  }
}

resource "aws_iam_role_policy" "artifact_read" {
  name   = "${var.name}-envoy-artifact-read"
  role   = aws_iam_role.envoy.name
  policy = data.aws_iam_policy_document.artifact_read.json
}

resource "aws_iam_instance_profile" "envoy" {
  name = "${var.name}-envoy"
  role = aws_iam_role.envoy.name
}

resource "aws_instance" "envoy" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.envoy.id]
  iam_instance_profile   = aws_iam_instance_profile.envoy.name

  associate_public_ip_address = false
  user_data                   = var.user_data
  # Re-run user_data when the install script or Envoy version changes.
  user_data_replace_on_change = true

  metadata_options {
    http_tokens = "required" # IMDSv2 only
  }

  root_block_device {
    encrypted = true
  }

  tags = merge(var.tags, { Name = "${var.name}-envoy" })
}
