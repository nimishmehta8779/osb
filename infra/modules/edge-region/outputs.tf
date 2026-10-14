output "nlb_dns_name" {
  value = aws_lb.edge.dns_name
}

output "nlb_zone_id" {
  description = "The NLB's own hosted zone ID, for Route 53 ALIAS targets. Not a hardcoded per-region constant: Terraform reads it from the live resource."
  value       = aws_lb.edge.zone_id
}

output "endpoint" {
  value = "${var.record_name}.${var.zone_name}"
}

output "nlb_arn_suffix" {
  description = "For CloudWatch NLB metric dimensions (LoadBalancer)."
  value       = aws_lb.edge.arn_suffix
}

output "envoy_target_group_arn_suffix" {
  description = "For CloudWatch NLB metric dimensions (TargetGroup), the plain-TCP listener's group."
  value       = aws_lb_target_group.envoy.arn_suffix
}

output "envoy_tls_target_group_arn_suffix" {
  description = "For CloudWatch NLB metric dimensions (TargetGroup), the TLS listener's group."
  value       = aws_lb_target_group.envoy_tls.arn_suffix
}
