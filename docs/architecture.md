# Platform Architecture & Design Invariants

This document specifies the technical architecture and core invariants of the **Open Service Broker (OSB) Edge Platform**.

---

## 1. The Core Architectural Thesis

Traditional cloud architectures treat edge ingress as dedicated infrastructure: each internal microservice provisions an Application Load Balancer (ALB), an ACM certificate, Route 53 DNS records, and dedicated WAF rules. 

At scale, this model causes:
- **Cost inefficiency:** Idle baseline charges and Load Balancer Capacity Units (LCUs) multiply across hundreds of services.
- **Quota & IP exhaustion:** Subnet CIDR blocks run out of IP addresses for Elastic Network Interfaces (ENIs).
- **Security drift:** Fragmented cipher suites and inconsistent edge access policies.
- **Operational friction:** Provisioning tickets and Terraform execution bottlenecks.

The OSB Edge Platform replaces this with a fundamental separation of concerns:

> **The platform owns the infrastructure. Product teams own the service intent.**

```
[Traditional Model]
Product Team ──► Provisions ALB ──► Provisions DNS ──► Provisions ACM ──► Manages Lifecycle

[OSB Model]
Product Team ──► Submits Declarative Intent (JSON) ──► Shared Edge Platform Routes Traffic
```

---

## 2. The Three Autonomous Planes

The architecture divides responsibilities into three planes operating at decoupled cadences with strictly isolated failure boundaries.

```
+-----------------------------------------------------------------------------------+
|                                  THE THREE PLANES                                 |
+-----------------------------------------------------------------------------------+
| 1. PROVISIONING PLANE (Minute Cadence)                                            |
|    - Components: Service Broker (FastAPI), SQS Queue + DLQ, Worker Daemon         |
|    - Duty: Accepts declarative service intent via OSB API, returns HTTP 202,     |
|      and coordinates cross-AWS transactional provisioning sagas.                  |
|    - Failure Domain: Provisioning outages never affect existing routes or config. |
+-----------------------------------------------------------------------------------+
| 2. CONTROL PLANE (Second Cadence)                                                 |
|    - Components: Sovereign (xDS server), DynamoDB Active Context, S3 Templates   |
|    - Duty: Compiles ACTIVE service intent into dynamic Envoy v3 discovery files.  |
|    - Failure Domain: Control plane crash prevents updates, but traffic is immune. |
+-----------------------------------------------------------------------------------+
| 3. DATA PLANE (Microsecond Cadence)                                               |
|    - Components: CloudFront, NLB (L4 pass-through), Envoy Proxy Cells, LKG Agent   |
|    - Duty: Terminates TLS, enforces rate limits, proxies requests to backends.   |
|    - Failure Domain: Statically stable; operates purely from local encrypted disk.|
+-----------------------------------------------------------------------------------+
```

---

## 3. Static Stability & The Last-Known-Good Pattern

In distributed systems, **static stability** means that a running system continues to operate normally when its control systems become unavailable.

### The Problem with Direct Dynamic xDS
If Envoy proxies connect directly to a remote management server via gRPC xDS, any network partition, database outage, or control plane crash risks halting traffic routing, dropping listener state, or preventing newly launched instances from serving traffic.

### The OSB Solution: Local LKG Filesystem Discovery
In the OSB Edge Platform, Envoy reads its discovery configurations directly from local encrypted disk (`/var/lib/osb/lkg/`):

```
                                  CONTROL PLANE (Remote)
                                +------------------------+
                                |    Sovereign (xDS)     |
                                +------------------------+
                                            |
                                  Polls every 15s (HTTP)
                                            v
==================================== ENVOY INSTANCE ====================================
                                +------------------------+
                                |    Local LKG Agent     |
                                |  (envoy/lkg-agent)     |
                                +------------------------+
                                     |                |
                    1. Validates     |                | 4. Forces rewrite
                       Schema/Keys   v                |    every 60s
                        [ /var/lib/osb/lkg/*.tmp ]    |
                                     |                |
                    2. Atomic Rename |                |
                       (os.replace)  v                |
                        [ /var/lib/osb/lkg/lds.json ] <+
                        [ /var/lib/osb/lkg/cds.json ]
                                     |
                                     | 3. inotify / path_config_source
                                     v
                                +------------------------+
                                |      Envoy Proxy       |
                                |  (Zero Remote Runtime  |
                                |       Dependency)      |
                                +------------------------+
```

### Key Invariants of the LKG Daemon:
1. **Validation Gate:** The LKG agent inspects every payload received from Sovereign. It validates that the JSON contains non-empty discovery arrays (`resources`, `version_info`). Empty or corrupted configurations are rejected before touching disk.
2. **Atomic Replacement:** Configuration files are written to temporary staging files on the same filesystem (`.tmp`) and swapped atomically using `os.replace()`. Envoy's `inotify` watcher never observes a partially written file.
3. **Bounded Self-Healing:** The agent performs a forced filesystem write every 60 seconds even if configuration content has not changed. This bounds configuration drift and triggers Envoy to re-read files if local state was disturbed.
4. **Zero-Dependency Survivability:** If Sovereign, S3, DynamoDB, or the internal network crashes, Envoy continues proxying 100% of live traffic without missing a single packet.

---

## 4. Multi-AZ Cell-Based Architecture

To limit blast radius, Envoy proxies are deployed in isolated **Availability Zone (AZ) Cells**:

```
                              [ CloudFront Perimeter ]
                                         │
                                         ▼
                         [ AWS Network Load Balancer (L4) ]
                                  (Cross-Zone OFF)
                           ┌─────────────┴─────────────┐
                           ▼                           ▼
                   ┌───────────────┐           ┌───────────────┐
                   │   AZ Cell 1   │           │   AZ Cell 2   │
                   │ (us-east-1a)  │           │ (us-east-1b)  │
                   │               │           │               │
                   │ Envoy Proxy 1 │           │ Envoy Proxy 2 │
                   │ Local LKG     │           │ Local LKG     │
                   └───────┬───────┘           └───────┬───────┘
                           │                           │
                           ▼                           ▼
                   [ Backend VPC Pod ]         [ Backend VPC Pod ]
```

- **Blast Radius Containment:** A catastrophic hardware or configuration fault inside one cell is strictly contained to that AZ.
- **Cross-Zone Minimization:** By aligning NLB target routing to local AZ subnets, inter-AZ data transfer fees and network latency are eliminated.
- **Canary Deployments:** New Envoy configurations and binary versions can be rolled out cell-by-cell.

---

## 5. The Transactional Provisioning Saga

Cross-service cloud provisioning (Route 53 DNS + ACM Certificates + CloudFront Distributions + DynamoDB State) cannot be wrapped in a single database transaction. The platform models this workflow as an **Asynchronous Distributed Saga** executed by the Worker daemon.

### Saga Sequence:

```
Product Team        Service Broker             SQS Queue               Worker Daemon          AWS Resources
     │                     │                       │                         │                      │
     │── PUT /v2/... ─────>│                       │                         │                      │
     │<── 202 Accepted ────│                       │                         │                      │
     │   (Operation ID)    │── Enqueue Message ───>│                         │                      │
     │                     │                       │── Poll Message ────────>│                      │
     │                                                                       │── 1. Reserve Name ──>│ (DynamoDB)
     │                                                                       │── 2. Request Cert ──>│ (ACM)
     │                                                                       │── 3. Create DNS ────>│ (Route 53)
     │                                                                       │── 4. Bind Route ────>│ (CloudFront)
     │                                                                       │── 5. Health Check ──>│ (Envoy Probe)
     │                                                                       │                      │
     │                                                                       │── 6. Mark ACTIVE ───>│ (DynamoDB)
     │                                                                       │                      │
     │── GET last_op ─────>│                                                 │                      │
     │<── state: succeeded─│                                                 │                      │
```

### Compensating Transactions & Failure Isolation:
- If any step fails (e.g., DNS validation timeout or CloudFront distribution limit hit), the Worker halts execution and triggers **backward compensation**.
- Preceding resources are deleted in reverse order (CloudFront route removed ➔ Route 53 record deleted ➔ ACM request cancelled ➔ DynamoDB lock released).
- The service instance state transitions to `FAILED`.
- **Quarantine Invariant:** Sovereign queries DynamoDB *only* for records where `status = "ACTIVE"`. Failing, pending, or quarantined tenants never appear in Envoy configuration snapshots.

---

## 6. Multi-Tenant Traffic Isolation & Guardrails

When hundreds of microservices share a single edge fleet, "noisy neighbor" protection is an absolute requirement.

### 1. Request Rate Limiting (`local_ratelimit`)
Each tenant is provisioned with an isolated token-bucket rate limiter evaluated inside Envoy:
- Configured via `rate_limit_rps` in the service spec.
- Enforced at the Envoy virtual host route level.
- Excess requests immediately return `HTTP 429 Too Many Requests` without touching backend workloads.

### 2. Bandwidth Throttling (`bandwidth_limit`)
- Configured via `bandwidth_kbps`.
- Protects the edge network from saturation caused by heavy data egress or file transfers.

### 3. Upstream Circuit Breaking
Every backend cluster is configured with strict connection caps:
- `max_connections`: Hard limit on concurrent TCP connections to upstream instances.
- `max_pending_requests`: Limit on queued requests waiting for an available backend connection.
- `max_requests`: Concurrent active HTTP requests.
- When an upstream service degrades, Envoy's circuit breaker trips, returning `HTTP 503 Service Unavailable` immediately rather than exhausting Envoy thread pools.

---

## 7. Security Architecture

1. **Edge Perimeter:** Amazon CloudFront handles AWS Shield standard DDoS protection and terminates global TLS.
2. **Layer 4 Handoff:** AWS Network Load Balancer (NLB) provides high-throughput, low-latency TCP forwarding without Layer 7 inspection or certificate storage.
3. **Private Ingress:** Envoy instances run inside private VPC subnets with ingress restricted strictly to the NLB security group.
4. **API Authentication:** Service Broker endpoints require an `X-Api-Key` header. Keys are hashed with SHA-256 before lookup in DynamoDB; plaintext keys are never stored.
5. **Team Boundary Isolation:** Every service instance is tagged with its provisioning `team_id`. Teams can only query, modify, or deprovision their own instances.
