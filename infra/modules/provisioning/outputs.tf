output "tenants_table" {
  value = aws_dynamodb_table.tenants.name
}

output "hostnames_table" {
  value = aws_dynamodb_table.hostnames.name
}

output "api_keys_table" {
  value = aws_dynamodb_table.api_keys.name
}

output "provision_queue_url" {
  value = aws_sqs_queue.provision.url
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "dlq_name" {
  value = aws_sqs_queue.dlq.name
}

output "broker_policy_arn" {
  value = aws_iam_policy.broker.arn
}

output "worker_policy_arn" {
  value = aws_iam_policy.worker.arn
}
