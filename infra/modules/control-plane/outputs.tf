output "instance_id" {
  value = aws_instance.sovereign.id
}

output "private_ip" {
  value = aws_instance.sovereign.private_ip
}

output "endpoint" {
  value = "${var.record_name}.${var.zone_name}"
}
