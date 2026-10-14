# Generic app host: one EC2 instance that pulls its build context from S3 at
# boot, builds a Docker image, and runs it. Used for the broker and the
# worker, which are identical in shape (one process, one image,
# least-privilege IAM) and differ only in their artifact, env, and ports.
# Sovereign (infra/modules/control-plane) predates this module and isn't
# migrated to it, to avoid touching working code.
#
# Files used to be embedded directly in user_data as base64. That broke the
# moment the broker's code grew past EC2's 16KB raw user_data limit (found
# live on the first real apply of this stack, 2026-10-14: broker's rendered
# script was ~31KB, worker's ~38KB -- both silently failed to launch). S3 has
# no such limit.

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_security_group" "app" {
  name        = var.name
  description = "${var.name}: only the ports it serves, from the VPC"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = var.name })
}

resource "aws_vpc_security_group_ingress_rule" "app" {
  for_each = { for p in var.container_ports : tostring(p.host) => p }

  security_group_id = aws_security_group.app.id
  cidr_ipv4         = var.vpc_cidr
  from_port         = each.value.host
  to_port           = each.value.host
  ip_protocol       = "tcp"
  description       = "${var.name} port ${each.value.host} from VPC"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.app.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "Outbound via NAT (image build, AWS API calls)"
}

resource "aws_iam_role" "app" {
  name = var.name

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

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

data "aws_iam_policy_document" "artifact_read" {
  statement {
    sid       = "ReadOwnBuildArtifact"
    actions   = ["s3:GetObject"]
    resources = ["${var.artifact_s3_bucket_arn}/*"]
  }
}

resource "aws_iam_role_policy" "artifact_read" {
  name   = "${var.name}-artifact-read"
  role   = aws_iam_role.app.name
  policy = data.aws_iam_policy_document.artifact_read.json
}

resource "aws_iam_role_policy_attachment" "extra" {
  # count, not for_each+toset: the ARNs come from the provisioning module's
  # policies and aren't known until apply, but the list's length is known at
  # plan time, which is all count needs.
  count = length(var.iam_policy_arns)

  role       = aws_iam_role.app.name
  policy_arn = var.iam_policy_arns[count.index]
}

resource "aws_iam_instance_profile" "app" {
  name = var.name
  role = aws_iam_role.app.name
}

resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.app.name

  associate_public_ip_address = false
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    artifact_s3_bucket = var.artifact_s3_bucket
    artifact_s3_key    = var.artifact_s3_key
    image_name         = var.image_name
    dockerfile         = var.dockerfile
    env                = var.container_env
    ports              = var.container_ports
    log_group          = var.log_group
    region             = var.region
  })
  user_data_replace_on_change = true

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    encrypted = true
  }

  tags = merge(var.tags, { Name = var.name })
}
