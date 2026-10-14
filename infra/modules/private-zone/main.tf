# Private hosted zone: resolvable only from inside the associated VPC.
# No public zone is created here, so no public DNS records exist.

resource "aws_route53_zone" "private" {
  name    = var.zone_name
  comment = "Internal-only zone for ${var.name}"

  vpc {
    vpc_id = var.vpc_id
  }

  tags = merge(var.tags, { Name = "${var.name}-internal" })
}
