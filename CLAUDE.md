# CLAUDE.md - Developer & AI Assistant Guide

This document provides project context, architecture invariants, and common commands for developers and AI coding assistants working in this repository.

---

## 1. Project Overview & Architecture

**Open Service Broker (OSB) Edge Platform** is a self-service edge platform where services are modeled as **declarative configuration, not infrastructure**.

### The Three Autonomous Planes
- **Data Plane (Microseconds):** CloudFront (edge DDoS/TLS) ➔ NLB (L4 pass-through, zero HTTP inspection) ➔ Envoy Auto Scaling Group partitioned into Availability Zone cells (Cell 1, Cell 2, Cell 3) ➔ Backend clusters in private VPC subnets.
- **Control Plane (Seconds):** Sovereign (FastAPI xDS server) fetches active tenant context from DynamoDB and renders Jinja2 templates into versioned xDS discovery snapshots (LDS, CDS, RDS).
- **Provisioning Plane (Minutes):** Service Broker (FastAPI) receives tenant specifications, writes `PENDING` to DynamoDB, and enqueues tasks to SQS. A Worker daemon executes asynchronous sagas across Route 53, ACM, and CloudFront with automatic backward compensation on failure.

### Core Invariants
1. **Static Stability (Last-Known-Good):** Live customer traffic has **zero runtime dependency** on the control plane. The local LKG agent (`envoy/lkg-agent/agent.py`) polls Sovereign, validates configurations, and writes atomically to local encrypted EBS (`/var/lib/osb/lkg/lds.json`). If Sovereign or databases are down, Envoy continues routing 100% of live traffic from local disk.
2. **Quarantine Unready Tenants:** Sovereign only reads `ACTIVE` tenants from the broker (`/internal/context/tenants`). Tenants undergoing provisioning (`PENDING` or `FAILED`) are never compiled into Envoy configurations.
3. **Cell Isolation & Shuffle Sharding:** 1 Cell = 1 AZ Envoy fleet. Tenants are isolated using local token-bucket rate limits (`local_ratelimit`), bandwidth caps, and backend circuit breakers (`max_connections`, `max_pending_requests`).

---

## 2. Common Commands

All common lifecycle operations are managed via the root `./deploy.sh` script:

### Local Development
```bash
# Start full 9-container mock platform via Docker Compose
./deploy.sh local-up

# Stop and clean up local containers and volumes
./deploy.sh local-down

# Check running status of containers or AWS resources
./deploy.sh status
```

### Testing
```bash
# Run all unit tests (LKG agent and Services)
./deploy.sh test

# Run LKG agent tests directly
cd envoy/lkg-agent && python3 -m unittest test_agent

# Run services tests directly (requires moto, fastapi, boto3)
PYTHONPATH=. python3 -m unittest \
  services.common.tests.test_store \
  services.broker.tests.test_api \
  services.worker.tests.test_saga \
  services.worker.tests.test_worker \
  services.sovereign.tests.test_templates
```

### Infrastructure (Terraform)
```bash
# Initialize Terraform providers and modules
./deploy.sh init

# Run Terraform plan against AWS
./deploy.sh plan

# Apply infrastructure to AWS (prompts for confirmation)
./deploy.sh apply

# Destroy provisioned AWS infrastructure (prompts for confirmation)
./deploy.sh destroy

# Check Terraform formatting & validation directly
terraform fmt -recursive -check infra
(cd infra && terraform validate)
```

### Team Key Administration
```bash
# Register/seed a new team API key in DynamoDB (stores SHA-256 hash)
./deploy.sh seed-key <team_id> <raw_api_key>
```

---

## 3. Code Style & Engineering Standards

- **Python:** Python 3.12+, PEP 8 formatting. Use type annotations (`typing` / Pydantic v2). Asynchronous endpoints where I/O bound (FastAPI `async def`).
- **Idempotency:** All provisioning operations and Route 53/DynamoDB adapters must be strictly idempotent. Use conditional expressions (`attribute_not_exists`) for atomic locking.
- **Terraform:** Terraform `>= 1.6`. Modules are located under `infra/modules/` and orchestrated directly from `infra/main.tf`. Use snake_case for resources and variables. Always include default tags (`project = "osb"`, `managed_by = "terraform"`).
- **Security:** Never commit plaintext credentials, AWS keys, or `.auto.tfvars` files. Store API keys as SHA-256 hashes. Restrict EC2 access via AWS Systems Manager (SSM Session Manager)—zero open SSH (port 22).
