# Open Service Broker (OSB) API Reference

The OSB Edge Platform implements a subset of the [Open Service Broker API (v2.16)](https://github.com/openservicebrokerapi/servicebroker) specification tailored for declarative cloud edge routing and load balancing.

---

## Authentication & Authorization

All requests to the Service Broker API require an API key passed in the `X-Api-Key` header:

```http
X-Api-Key: your-team-api-key-here
```

### Security Properties
- **SHA-256 Hashing:** The broker hashes incoming keys using SHA-256 and queries DynamoDB (`<name>-api-keys`). Plaintext keys are never stored.
- **Team Boundary Enforcement:** Each key resolves to a unique `team_id`. A team can only create, poll, or deprovision service instances that belong to its `team_id`. Attempting to access an instance owned by another team returns `HTTP 403 Forbidden`.

---

## API Endpoints

### 1. Retrieve Service Catalog

Returns the list of service offerings and service plans available on the edge platform.

```http
GET /v2/catalog
```

#### Request Headers
| Header | Type | Description |
| :--- | :--- | :--- |
| `X-Api-Key` | String | **Required.** Your team API key. |

#### Response (`HTTP 200 OK`)
```json
{
  "services": [
    {
      "name": "edge-ingress",
      "id": "web-api",
      "description": "Hardened multi-tenant enterprise edge ingress routing with rate limiting and TLS.",
      "bindable": false,
      "plans": [
        {
          "name": "standard",
          "id": "standard-plan",
          "description": "Standard edge routing with configurable rate limits.",
          "free": true
        }
      ]
    }
  ]
}
```

---

### 2. Provision or Update Service Instance

Declaratively publishes a backend microservice to the shared edge platform. Because edge provisioning (DNS validation, certificate issuance, CloudFront routing) takes tens of seconds, this endpoint executes asynchronously.

```http
PUT /v2/service_instances/{instance_id}?accepts_incomplete=true
```

#### Path Parameters
| Parameter | Type | Description |
| :--- | :--- | :--- |
| `instance_id` | String | Unique slug identifying your service instance (e.g., `payment-service-prod`). |

#### Query Parameters
| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `accepts_incomplete` | Boolean | **Yes** | Must be `true` to signal asynchronous handling. |

#### Request Headers
| Header | Type | Description |
| :--- | :--- | :--- |
| `X-Api-Key` | String | **Required.** Your team API key. |
| `Content-Type` | String | Must be `application/json`. |

#### Request Body Schema
```json
{
  "service_id": "web-api",
  "plan_id": "standard-plan",
  "parameters": {
    "hostname": "billing.example.internal",
    "backend_host": "billing-backend.prod.internal",
    "backend_port": 8080,
    "rate_limit_rps": 250,
    "bandwidth_kbps": 50000
  }
}
```

#### Parameters Reference
| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `hostname` | String | *Required* | Fully qualified domain name (FQDN) for the service. Must be unique across the platform. |
| `backend_host` | String | *Required* | Private IP address or VPC internal DNS of your target workload. |
| `backend_port` | Integer | *Required* | TCP port your service listens on (e.g., `80`, `8080`, `3000`). |
| `rate_limit_rps` | Integer | `100` | Maximum requests per second allowed before Envoy returns `HTTP 429`. |
| `bandwidth_kbps`| Integer | `10000` | Maximum egress throughput in kilobits per second. |

#### Response (`HTTP 202 Accepted`)
```json
{
  "operation": "op-550e8400-e29b-41d4-a716-446655440000"
}
```

---

### 3. Poll Asynchronous Operation Status

Queries the status of an ongoing or completed provisioning saga.

```http
GET /v2/service_instances/{instance_id}/last_operation?operation={operation_id}
```

#### Path Parameters
| Parameter | Type | Description |
| :--- | :--- | :--- |
| `instance_id` | String | Service instance identifier. |

#### Query Parameters
| Parameter | Type | Description |
| :--- | :--- | :--- |
| `operation` | String | The operation ID returned by the initial `PUT` or `DELETE` request. |

#### Response (`HTTP 200 OK`)

**When Provisioning is In Progress:**
```json
{
  "state": "in progress",
  "description": "Worker executing transactional provisioning saga: validating Route 53 and ACM"
}
```

**When Provisioning Succeeded:**
```json
{
  "state": "succeeded",
  "description": "Provisioning complete. Route is ACTIVE on edge fleet."
}
```

**When Provisioning Failed (Compensated):**
```json
{
  "state": "failed",
  "description": "Provisioning failed: backend health check probe timed out. Preceding resources successfully compensated."
}
```

---

### 4. Deprovision Service Instance

Asynchronously tears down the edge routing, releases DNS records, and removes the tenant from Envoy configuration snapshots.

```http
DELETE /v2/service_instances/{instance_id}?accepts_incomplete=true
```

#### Response (`HTTP 202 Accepted`)
```json
{
  "operation": "op-decom-99812-77123"
}
```

---

## Status Codes & Error Handling

The broker returns standard HTTP status codes accompanied by a JSON error envelope:

| Status Code | Meaning | Common Cause |
| :--- | :--- | :--- |
| `200 OK` | Synchronous operation completed | Service catalog fetched or synchronous operation completed. |
| `202 Accepted` | Asynchronous job scheduled | Provisioning or deprovisioning saga enqueued to SQS. |
| `400 Bad Request` | Malformed payload | Missing required parameter (`hostname`, `backend_host`, `backend_port`). |
| `401 Unauthorized` | Invalid authentication | Missing `X-Api-Key` header or unseeded API key. |
| `403 Forbidden` | Access denied | The requesting `team_id` does not own the target `instance_id`. |
| `404 Not Found` | Instance not found | Querying status for a non-existent `instance_id`. |
| `409 Conflict` | Conflicting resource | The requested `hostname` is already claimed by another active tenant. |
| `500 Internal Error` | Server error | SQS unavailable, DynamoDB throttling, or unexpected exception. |

### Error Response Schema
```json
{
  "error": "Conflict",
  "description": "Hostname 'billing.example.internal' is already claimed by instance 'tenant-billing-core'."
}
```

---

## Example Usage with cURL

### Provision a New Service
```bash
curl -X PUT http://localhost:8000/v2/service_instances/order-service \
  -H "X-Api-Key: local-dev-only" \
  -H "Content-Type: application/json" \
  -d '{
    "service_id": "web-api",
    "parameters": {
      "hostname": "orders.example.internal",
      "backend_host": "order-api.internal",
      "backend_port": 8080,
      "rate_limit_rps": 500,
      "bandwidth_kbps": 20000
    }
  }'
```

### Check Operation Progress
```bash
curl -s http://localhost:8000/v2/service_instances/order-service/last_operation \
  -H "X-Api-Key: local-dev-only"
```

### Delete Service
```bash
curl -X DELETE "http://localhost:8000/v2/service_instances/order-service?accepts_incomplete=true" \
  -H "X-Api-Key: local-dev-only"
```
