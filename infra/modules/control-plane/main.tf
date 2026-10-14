# Sovereign control plane: one instance in a private subnet, reachable only inside the VPC.
# Serves xDS listeners over REST on port 8080. Built from services/sovereign/ at boot.

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_security_group" "sovereign" {
  name        = "${var.name}-sovereign"
  description = "Sovereign: xDS from the VPC only"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-sovereign" })
}

resource "aws_vpc_security_group_ingress_rule" "xds" {
  security_group_id = aws_security_group.sovereign.id
  cidr_ipv4         = var.vpc_cidr
  from_port         = 8080
  to_port           = 8080
  ip_protocol       = "tcp"
  description       = "xDS REST from VPC (Envoy hosts via agent)"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.sovereign.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "Outbound via NAT (image build, package install)"
}

resource "aws_iam_role" "sovereign" {
  name = "${var.name}-sovereign"

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
  role       = aws_iam_role.sovereign.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  role       = aws_iam_role.sovereign.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

resource "aws_iam_instance_profile" "sovereign" {
  name = "${var.name}-sovereign"
  role = aws_iam_role.sovereign.name
}

resource "aws_instance" "sovereign" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.sovereign.id]
  iam_instance_profile   = aws_iam_instance_profile.sovereign.name

  associate_public_ip_address = false
  user_data                   = var.user_data
  user_data_replace_on_change = true

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    encrypted = true
  }

  tags = merge(var.tags, { Name = "${var.name}-sovereign" })
}

# Private DNS name for the control plane. Envoy hosts resolve it inside the VPC only.
resource "aws_route53_record" "sovereign" {
  zone_id = var.private_zone_id
  name    = "${var.record_name}.${var.zone_name}"
  type    = "A"
  ttl     = 60
  records = [aws_instance.sovereign.private_ip]
}
