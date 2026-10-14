# Provisioning plane state and messaging: tenant table, hostname table, queue, dead-letter queue.
# Broker and worker IAM is least-privilege: each gets only the actions it uses.

resource "aws_dynamodb_table" "tenants" {
  name         = "${var.name}-tenants"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "instance_id"

  attribute {
    name = "instance_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(var.tags, { Name = "${var.name}-tenants" })
}

resource "aws_dynamodb_table" "hostnames" {
  name         = "${var.name}-hostnames"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "hostname"

  attribute {
    name = "hostname"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(var.tags, { Name = "${var.name}-hostnames" })
}

resource "aws_dynamodb_table" "api_keys" {
  name         = "${var.name}-api-keys"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "api_key_hash"

  attribute {
    name = "api_key_hash"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(var.tags, { Name = "${var.name}-api-keys" })
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-provision-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum, so failed messages can be inspected
  sqs_managed_sse_enabled   = true

  tags = merge(var.tags, { Name = "${var.name}-provision-dlq" })
}

resource "aws_sqs_queue" "provision" {
  name                       = "${var.name}-provision"
  visibility_timeout_seconds = 120 # longer than one saga pass, so in-flight work is not redelivered early
  message_retention_seconds  = 345600
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 5
  })

  tags = merge(var.tags, { Name = "${var.name}-provision" })
}

# ---- IAM: broker (enqueue + read state) ----

data "aws_iam_policy_document" "broker" {
  statement {
    sid       = "EnqueueProvisioning"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.provision.arn]
  }
  statement {
    sid       = "ReadTenantState"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan"]
    resources = [aws_dynamodb_table.tenants.arn]
  }
  statement {
    sid       = "ResolveApiKeyTeam"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.api_keys.arn]
  }
}

resource "aws_iam_policy" "broker" {
  name   = "${var.name}-broker"
  policy = data.aws_iam_policy_document.broker.json
}

# ---- IAM: worker (consume, write state, manage tenant DNS) ----

data "aws_iam_policy_document" "worker" {
  statement {
    sid       = "ConsumeProvisioning"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
    resources = [aws_sqs_queue.provision.arn]
  }
  statement {
    sid       = "WriteTenantState"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.tenants.arn, aws_dynamodb_table.hostnames.arn]
  }
  statement {
    sid       = "ManageTenantRecordsOnly"
    actions   = ["route53:ChangeResourceRecordSets", "route53:ListResourceRecordSets"]
    resources = [var.private_zone_arn]
  }
}

resource "aws_iam_policy" "worker" {
  name   = "${var.name}-worker"
  policy = data.aws_iam_policy_document.worker.json
}
