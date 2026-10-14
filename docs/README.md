# OSB Edge Platform Documentation

Welcome to the **Open Service Broker (OSB) Edge Platform** documentation suite. This platform provides declarative, self-service edge load balancing on AWS, decoupling service publishing from infrastructure provisioning.

---

## Documentation Index

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| **[Architecture Guide](architecture.md)** | Deep dive into the Three Autonomous Planes, Static Stability (LKG pattern), Cell-Based isolation, and Distributed Transactional Sagas. | Architects, Principal Engineers, Platform Teams |
| **[API Reference](api-reference.md)** | Complete specification of the Open Service Broker REST API (`/v2/catalog`, `/v2/service_instances`), request/response schemas, and authentication. | Application Developers, Service Owners, API Consumers |
| **[Operations & Runbooks](operations.md)** | Day-2 operations guide: local Docker development, AWS Terraform deployment, API key management, CloudWatch observability, and disaster recovery. | SREs, DevOps Engineers, Cloud Operations |
| **[Engineering Case Study](engineering-story.md)** | Narrative engineering story detailing the architectural journey from infrastructure sprawl to static stability (*"Services as Configuration, Not Infrastructure"*). | Engineering Leaders, Systems Architects |
| **[Master Architecture Diagram](diagrams/aws_edge_platform_production.png)** | High-resolution production architecture diagram with official AWS icons. Available in [PNG](diagrams/aws_edge_platform_production.png), [SVG](diagrams/aws_edge_platform_production.svg), and editable [Excalidraw](diagrams/aws_edge_platform_production.excalidraw). | All Engineers & Architects |

---

## Architectural Summary

```
                      +------------------------------------------+
                      |         Product Engineering Team         |
                      |        (Owns Declarative Intent)         |
                      +------------------------------------------+
                                           |
                                  HTTP PUT (API Key)
                                           v
+===================================================================================+
|                                PROVISIONING PLANE                                 |
|                                                                                   |
|   +--------------------+     SQS     +-------------------+     Reverse            |
|   |   Service Broker   | ==========> |   Worker Daemon   | <== Compensation       |
|   |  (HTTP 202 Async)  |             |  (Executes Saga)  |     on Failure         |
|   +--------------------+             +-------------------+                        |
+==================================================|================================+
                                                   | Writes ACTIVE
                                                   v Tenants
+===================================================================================+
|                                  CONTROL PLANE                                    |
|                                                                                   |
|           +---------------------+                   +-------------------+         |
|           | DynamoDB (Tenants)  |                   | S3 (Jinja2 Tpl)   |         |
|           +---------------------+                   +-------------------+         |
|                      \                                 /                          |
|                       v                               v                           |
|                      +---------------------------------+                          |
|                      |         Sovereign (xDS)         |                          |
|                      |  Compiles Dynamic Envoy Config  |                          |
|                      +---------------------------------+                          |
+=======================================|===========================================+
                                        | Polls every 15s
                                        v
+===================================================================================+
|                                   DATA PLANE                                      |
|                                                                                   |
|       [ CloudFront CDN (DDoS) ] ──► [ AWS Network Load Balancer (L4) ]            |
|                                                     |                             |
|                           +-------------------------+-------------------------+   |
|                           |                                                   |   |
|                           v                                                   v   |
|               +-----------------------+                           +-----------------------+
|               |  AZ Cell 1 (Envoy)    |                           |  AZ Cell 2 (Envoy)    |
|               |  +-----------------+  |                           |  +-----------------+  |
|               |  | Local LKG Agent |  |                           |  | Local LKG Agent |  |
|               |  +-----------------+  |                           |  +-----------------+  |
|               |          |            |                           |          |            |
|               |          v            |                           |          v            |
|               |  [ /var/lib/osb/lkg ] |                           |  [ /var/lib/osb/lkg ] |
|               |  (Static Stability)   |                           |  (Static Stability)   |
|               +-----------------------+                           +-----------------------+
|                           |                                                   |   |
|                           v                                                   v   |
|               [ Backend Workloads ]                               [ Backend Workloads ]   |
+===================================================================================+
```

---

## Core Principles

1. **Services as Configuration:** Product teams submit lightweight JSON specs rather than provisioning infrastructure stacks.
2. **Static Stability:** Envoy proxies read locally cached Last-Known-Good configuration from encrypted disk. If the control plane, database, or network fails, live customer traffic continues unhindered.
3. **No Unready Exposure:** Tenants must complete end-to-end verification and achieve `ACTIVE` status before they are exposed to ingress routing.
4. **Blast-Radius Containment:** Envoy clusters are isolated into Availability Zone cells with rate limiting and connection pooling per tenant.
