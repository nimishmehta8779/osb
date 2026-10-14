# Operations & Day-2 Engineering Runbook

This guide covers operational lifecycle procedures, local development, automated testing, production AWS deployment, observability, and disaster recovery runbooks for the **Open Service Broker (OSB) Edge Platform**.

---

## 1. Prerequisites

Before working with the platform, ensure the following tools are installed on your workstation:
- **Docker & Docker Compose:** Container runtime for local development.
- **Python 3.12+:** Required for unit testing and local CLI scripts (`pytest`, `boto3`, `fastapi`).
- **Terraform 1.6+:** Infrastructure as Code for AWS provisioning.
- **AWS CLI v2:** Configured with administrative or deployment IAM credentials.

---

## 2. Unified Deployment CLI (`deploy.sh`)

All lifecycle commands are managed through the root executable script:

```bash
./deploy.sh <command> [arguments]
```

### Command Reference

| Command | Arguments | Description |
| :--- | :--- | :--- |
| `local-up` | *None* | Spins up the complete 9-container mock stack via Docker Compose. |
| `local-down`| *None* | Gracefully stops containers, removes networks, and purges volumes. |
| `test` | *None* | Runs unit test suites for Services and the Envoy LKG Agent. |
| `plan` | `[tf-args...]` | Runs `terraform plan` against `infra/main.tf`. |
| `apply` | `[tf-args...]` | Deploys AWS infrastructure via `terraform apply`. |
| `destroy` | `[tf-args...]` | Tears down all AWS resources created by Terraform. |
| `seed-key` | `<team_id> <raw_key>` | Hashes and registers a team API key into DynamoDB. |
| `status` | *None* | Inspects container status (local) or outputs AWS resources. |

---

## 3. Local Development & Mock Environment

The local development environment uses `docker-compose.local.yml` to simulate all AWS services without requiring cloud credentials:

```
+-----------------------------------------------------------------------------------+
|                        LOCAL DOCKER COMPOSE MOCK STACK                            |
+-----------------------------------------------------------------------------------+
|  1. dynamo-local   : Amazon DynamoDB Local (tables: tenants, api-keys)            |
|  2. sqs-local      : ElasticMQ (simulating Amazon SQS and DLQ)                    |
|  3. s3-mock        : Local filesystem storage for Sovereign Jinja2 templates      |
|  4. osb-broker     : FastAPI Broker application (port 8000)                       |
|  5. osb-worker     : Saga execution worker daemon                                 |
|  6. osb-sovereign  : Dynamic Envoy xDS discovery generator (port 8001)            |
|  7. envoy-lkg      : Python LKG agent polling Sovereign (15s cadence)             |
|  8. osb-envoy      : Official Envoy Proxy container v1.39.2 (port 10000)          |
|  9. backend-demo   : Nginx container representing private backend workload        |
+-----------------------------------------------------------------------------------+
```

### Local Workflow Walkthrough

#### 1. Start the Stack
```bash
./deploy.sh local-up
```

#### 2. Run Test Suite
```bash
./deploy.sh test
```

#### 3. Test Static Stability Under Simulated Failure
To verify that customer traffic is immune to control-plane outages:

1. Provision a test tenant:
   ```bash
   curl -X PUT http://localhost:8000/v2/service_instances/test-tenant \
     -H "X-Api-Key: local-dev-only" \
     -H "Content-Type: application/json" \
     -d '{
       "service_id": "web-api",
       "parameters": {
         "hostname": "app1.osb.internal",
         "backend_host": "backend-demo",
         "backend_port": 80
       }
     }'
   ```
2. Verify traffic flows through Envoy:
   ```bash
   curl -H "Host: app1.osb.internal" http://localhost:10000/
   ```
3. Kill the control plane and database containers:
   ```bash
   docker stop osb-sovereign dynamo-local
   ```
4. Query Envoy again:
   ```bash
   curl -H "Host: app1.osb.internal" http://localhost:10000/
   # Returns 200 OK without latency spikes! Envoy serves from local LKG disk.
   ```
5. Inspect the local LKG snapshot inside the container:
   ```bash
   docker exec -it osb-envoy cat /var/lib/osb/lkg/lds.json
   ```

---

## 4. Production AWS Deployment

### Remote State Configuration
For production deployments, configure a remote S3 backend with DynamoDB state locking in `infra/main.tf`:

```hcl
terraform {
  backend "s3" {
    bucket         = "my-company-terraform-states"
    key            = "osb-edge/production.tfstate"
    region         = "us-east-1"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}
```

### Execution
```bash
# 1. Preview changes
./deploy.sh plan

# 2. Deploy to AWS
./deploy.sh apply
```

---

## 5. API Key Management & Team Onboarding

Teams must be registered before they can invoke the Service Broker API.

### Registering a New Team Key
```bash
./deploy.sh seed-key "payments-team" "sec-prod-key-99a8b7c6d5e4"
```

### How Keys Are Stored
The script hashes the raw key with SHA-256 and writes the record to DynamoDB:
- **Table:** `<name>-api-keys`
- **Partition Key (`key_hash`):** `SHA-256(raw_key)`
- **Attribute (`team_id`):** `"payments-team"`
- **Attribute (`created_at`):** ISO-8601 timestamp

Plaintext keys are never stored in DynamoDB or logged.

---

## 6. Observability & Monitoring

### CloudWatch Log Groups
All EC2 instances and containers forward logs to Amazon CloudWatch via the CloudWatch Agent:

| Log Group | Source | Retention |
| :--- | :--- | :--- |
| `/osb/<name>/broker` | FastAPI Broker stdout/stderr | 14 Days |
| `/osb/<name>/worker` | Saga Worker daemon stdout/stderr | 14 Days |
| `/osb/<name>/sovereign` | Sovereign xDS generator | 14 Days |
| `/osb/<name>/envoy` | Envoy proxy and LKG agent logs | 14 Days |

### CloudWatch Metric Alarms
Three automated CloudWatch alarms publish directly to an SNS alerting topic:

1. **Dead-Letter Queue Alarm (`<name>-dlq-depth`):**
   - **Metric:** `ApproximateNumberOfMessagesVisible` on DLQ > 0.
   - **Severity:** HIGH. Indicates a provisioning saga failed after all retry attempts.
2. **NLB Target Health Alarms (`<name>-nlb-unhealthy-plain`, `<name>-nlb-unhealthy-tls`):**
   - **Metric:** `UnHealthyHostCount` > 0 on NLB Target Groups.
   - **Severity:** CRITICAL. Indicates Envoy proxy instances are failing health checks.
3. **EC2 Status Check Alarms:**
   - **Metric:** `StatusCheckFailed_System` or `StatusCheckFailed_Instance` = 1.
   - **Severity:** HIGH. Underlying hardware or hypervisor degradation.

---

## 7. Disaster Recovery & Runbooks

### Runbook 1: Dead-Letter Queue (DLQ) Triage & Redrive
When `<name>-dlq-depth` fires, a provisioning saga has failed permanently.

1. **Inspect Dead-Letter Messages:**
   ```bash
   aws sqs receive-message \
     --queue-url $(aws sqs get-queue-url --queue-name "osb-provision-dlq" --query QueueUrl -o text) \
     --attribute-names All \
     --message-attribute-names All \
     --max-number-of-messages 10
   ```
2. **Diagnose the Failure:**
   - Look at the `error` and `step` attributes.
   - Common causes: AWS API rate limits, ACM certificate verification timeout, invalid DNS delegation.
3. **Redrive the Message:**
   Once the root cause is resolved (e.g., quota increase granted):
   ```bash
   aws sqs start-message-move-task \
     --source-arn "arn:aws:sqs:us-east-1:123456789012:osb-provision-dlq" \
     --destination-arn "arn:aws:sqs:us-east-1:123456789012:osb-provision-queue"
   ```

### Runbook 2: Bad Configuration Rollback & Containment
If Sovereign generates an invalid configuration (e.g., broken Jinja2 syntax):
1. **The LKG Safety Gate Prevents Rollout:**
   The local LKG agent on each Envoy instance validates discovery files before writing to disk. If validation fails, it discards the payload and keeps the previous `/var/lib/osb/lkg/*.json` active.
2. **Alert Trigger:** Check `/osb/<name>/envoy` for:
   ```
   [ERROR] Invalid configuration received from Sovereign: missing 'resources'. Discarding.
   ```
3. **Remediation:** Fix the template in `services/sovereign/templates/` or remove the corrupted tenant in DynamoDB (`status = "QUARANTINED"`). Sovereign will recompile a valid snapshot on the next poll.

### Runbook 3: Zero-Downtime Envoy Fleet Recycling
When updating Envoy binaries or AMI versions:
1. Envoy instances are bound to an NLB Target Group with health checks on port `10000` (`/healthz` or TCP).
2. Set deregistration delay to 30 seconds to allow inflight TCP streams to complete.
3. Terminate instances sequentially across AZ cells to preserve multi-zone redundancy.
