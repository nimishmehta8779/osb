output "instance_id" {
  value = aws_instance.envoy.id
}

output "private_ip" {
  value = aws_instance.envoy.private_ip
}

output "security_group_id" {
  value = aws_security_group.envoy.id
}
