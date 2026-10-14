# Internal NLB (L4 pass-through) in front of the Envoy fleet, plus a private DNS record.
# Port 80 is plain TCP. Port 443 is also plain TCP at the NLB (NLB does not
# terminate TLS) -- it passes straight through to Envoy's TLS listener
# (10443), which terminates with a self-signed cert. See docs/architecture.md
# for the production perimeter model (CloudFront + ACM + NLB).

resource "aws_lb" "edge" {
  name               = "${var.name}-edge"
  load_balancer_type = "network"
  internal           = true
  subnets            = var.subnet_ids

  enable_cross_zone_load_balancing = false
  enable_deletion_protection       = false

  tags = merge(var.tags, { Name = "${var.name}-edge" })
}

resource "aws_lb_target_group" "envoy" {
  name        = "${var.name}-envoy"
  port        = 10000
  protocol    = "TCP"
  target_type = "instance"
  vpc_id      = var.vpc_id

  # Health check on Envoy's admin /ready, not the listener.
  # Admin is LIVE only when Envoy has finished starting.
  health_check {
    protocol            = "HTTP"
    port                = "9901"
    path                = "/ready"
    matcher             = "200"
    interval            = 10
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }

  # Connection draining on deregistration (scale-in, replacement).
  deregistration_delay = 30

  # Client IP preservation stays on (the default). Decision (Stage L4, 2026-10-08):
  # the known hairpin limit -- an Envoy host can't reach its own NLB -- only
  # affects self-testing from the Envoy host itself. Real customers and every
  # other VPC host are unaffected (verified in Phase (b)). Turning preservation
  # off would fix the hairpin case but lose the real client IP for access logs,
  # rate limiting, and ext_authz decisions in Phase (e), which matters more.

  tags = merge(var.tags, { Name = "${var.name}-envoy" })
}

resource "aws_lb_target_group_attachment" "envoy" {
  target_group_arn = aws_lb_target_group.envoy.arn
  target_id        = var.instance_id
  port             = 10000
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.edge.arn
  port              = 80
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.envoy.arn
  }
}

resource "aws_lb_target_group" "envoy_tls" {
  name        = "${var.name}-envoy-tls"
  port        = 10443
  protocol    = "TCP"
  target_type = "instance"
  vpc_id      = var.vpc_id

  health_check {
    protocol            = "HTTP"
    port                = "9901"
    path                = "/ready"
    matcher             = "200"
    interval            = 10
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }

  deregistration_delay = 30

  tags = merge(var.tags, { Name = "${var.name}-envoy-tls" })
}

resource "aws_lb_target_group_attachment" "envoy_tls" {
  target_group_arn = aws_lb_target_group.envoy_tls.arn
  target_id        = var.instance_id
  port             = 10443
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.edge.arn
  port              = 443
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.envoy_tls.arn
  }
}

# Private DNS record: resolves only inside the VPC.
resource "aws_route53_record" "edge" {
  zone_id = var.private_zone_id
  name    = "${var.record_name}.${var.zone_name}"
  type    = "A"

  alias {
    name                   = aws_lb.edge.dns_name
    zone_id                = aws_lb.edge.zone_id
    evaluate_target_health = true
  }
}
