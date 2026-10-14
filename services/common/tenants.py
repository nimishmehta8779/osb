"""Tenant model and DynamoDB store shared by the broker and the worker.

Every state change is a conditional write. A write only succeeds if the record is
still in the state (and version) the caller read, so a retried or duplicated
message can't overwrite a newer result.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from botocore.exceptions import ClientError

PENDING = "PENDING"
ACTIVE = "ACTIVE"
FAILED = "FAILED"
DEPROVISIONING = "DEPROVISIONING"
DELETED = "DELETED"

# DNS label rules, plus a required suffix. The suffix is the private zone (osb.internal).
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


class Conflict(Exception):
    """A record exists with different content, or a write lost a race."""


class NotFound(Exception):
    pass


# Platform defaults applied when a tenant doesn't ask for a tier: every tenant
# gets isolation whether or not they requested it. A tenant can request a
# higher tier via `parameters`, but never an unlimited one through this path.
DEFAULT_BANDWIDTH_KBPS = 10_000  # 10 Mbps
DEFAULT_RATE_LIMIT_RPS = 100
MAX_BANDWIDTH_KBPS = 1_000_000  # 1 Gbps ceiling a tenant can request without an operator override
MAX_RATE_LIMIT_RPS = 10_000


@dataclass(frozen=True)
class ServiceSpec:
    service_id: str
    hostname: str
    backend_host: str
    backend_port: int
    team_id: str
    bandwidth_kbps: int = DEFAULT_BANDWIDTH_KBPS
    rate_limit_rps: int = DEFAULT_RATE_LIMIT_RPS

    @staticmethod
    def validate(data: dict, zone_suffix: str, team_id: Optional[str] = None) -> "ServiceSpec":
        hostname = str(data.get("hostname", "")).lower()
        if not hostname.endswith("." + zone_suffix):
            raise ValueError(f"hostname must end with .{zone_suffix}")
        label = hostname[: -len(zone_suffix) - 1]
        if not _LABEL.match(label):
            raise ValueError("hostname label is not a valid DNS label")
        port = data.get("backend_port")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("backend_port must be 1-65535")
        backend_host = str(data.get("backend_host", ""))
        if not backend_host:
            raise ValueError("backend_host required")
        service_id = str(data.get("service_id", ""))
        if not service_id:
            raise ValueError("service_id required")

        # team_id identifies who owns this instance for authorization (see
        # services/common/api_keys.py). The caller's resolved identity (from
        # its API key) always wins over anything in the request body, so a
        # team can never provision on another team's behalf by just naming it.
        resolved_team_id = str(team_id if team_id is not None else data.get("team_id", ""))
        if not resolved_team_id:
            raise ValueError("team_id required")

        bandwidth_kbps = ServiceSpec._bounded_int(
            data, "bandwidth_kbps", DEFAULT_BANDWIDTH_KBPS, MAX_BANDWIDTH_KBPS
        )
        rate_limit_rps = ServiceSpec._bounded_int(
            data, "rate_limit_rps", DEFAULT_RATE_LIMIT_RPS, MAX_RATE_LIMIT_RPS
        )

        return ServiceSpec(service_id, hostname, backend_host, port, resolved_team_id, bandwidth_kbps, rate_limit_rps)

    @staticmethod
    def _bounded_int(data: dict, key: str, default: int, maximum: int) -> int:
        if key not in data or data[key] is None:
            return default
        value = data[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{key} must be an integer")
        if not 1 <= value <= maximum:
            raise ValueError(f"{key} must be between 1 and {maximum}")
        return value

    def as_dict(self) -> dict:
        return {
            "service_id": self.service_id,
            "hostname": self.hostname,
            "backend_host": self.backend_host,
            "backend_port": self.backend_port,
            "team_id": self.team_id,
            "bandwidth_kbps": self.bandwidth_kbps,
            "rate_limit_rps": self.rate_limit_rps,
        }


@dataclass
class Tenant:
    instance_id: str
    state: str
    spec: dict
    operation_id: str
    version: int
    steps: dict = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def hostname(self) -> str:
        return self.spec["hostname"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TenantStore:
    def __init__(self, tenants_table, hostnames_table):
        self._tenants = tenants_table
        self._hostnames = hostnames_table

    # ---- tenants ----

    def create_pending(self, instance_id: str, spec: ServiceSpec, operation_id: str) -> Tenant:
        item = {
            "instance_id": instance_id,
            "state": PENDING,
            "spec": spec.as_dict(),
            "operation_id": operation_id,
            "version": 1,
            "steps": {},
            "updated_at": _now(),
        }
        try:
            self._tenants.put_item(Item=item, ConditionExpression="attribute_not_exists(instance_id)")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise Conflict("instance already exists") from e
            raise
        return self._to_tenant(item)

    def get(self, instance_id: str) -> Tenant:
        resp = self._tenants.get_item(Key={"instance_id": instance_id}, ConsistentRead=True)
        if "Item" not in resp:
            raise NotFound(instance_id)
        return self._to_tenant(resp["Item"])

    def transition(
        self,
        tenant: Tenant,
        new_state: str,
        *,
        steps: Optional[dict] = None,
        error: Optional[str] = None,
        operation_id: Optional[str] = None,
    ) -> Tenant:
        """Move to new_state only if the record is still at tenant.version."""
        names = {"#state": "state", "#version": "version", "#updated": "updated_at"}
        values: dict[str, Any] = {
            ":state": new_state,
            ":next": tenant.version + 1,
            ":expected": tenant.version,
            ":now": _now(),
        }
        sets = ["#state = :state", "#version = :next", "#updated = :now"]
        if steps is not None:
            names["#steps"] = "steps"
            values[":steps"] = steps
            sets.append("#steps = :steps")
        if error is not None:
            names["#error"] = "error"
            values[":error"] = error
            sets.append("#error = :error")
        if operation_id is not None:
            names["#operation_id"] = "operation_id"
            values[":operation_id"] = operation_id
            sets.append("#operation_id = :operation_id")
        try:
            resp = self._tenants.update_item(
                Key={"instance_id": tenant.instance_id},
                UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression="#version = :expected",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise Conflict("tenant changed concurrently") from e
            raise
        return self._to_tenant(resp["Attributes"])

    def active_tenants(self) -> list[Tenant]:
        """Only ACTIVE tenants are served to Sovereign. Half-provisioned tenants never reach Envoy."""
        out: list[Tenant] = []
        kwargs: dict[str, Any] = {"FilterExpression": "#state = :active", "ExpressionAttributeNames": {"#state": "state"}, "ExpressionAttributeValues": {":active": ACTIVE}}
        while True:
            resp = self._tenants.scan(**kwargs)
            out.extend(self._to_tenant(i) for i in resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                return out
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # ---- hostname reservation (one owner per hostname) ----

    def reserve_hostname(self, hostname: str, instance_id: str) -> None:
        try:
            self._hostnames.put_item(
                Item={"hostname": hostname, "instance_id": instance_id},
                ConditionExpression="attribute_not_exists(hostname) OR instance_id = :me",
                ExpressionAttributeValues={":me": instance_id},
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise Conflict(f"hostname {hostname} is owned by another instance") from e
            raise

    def release_hostname(self, hostname: str, instance_id: str) -> None:
        try:
            self._hostnames.delete_item(
                Key={"hostname": hostname},
                ConditionExpression="instance_id = :me",
                ExpressionAttributeValues={":me": instance_id},
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    @staticmethod
    def _to_tenant(item: dict) -> Tenant:
        return Tenant(
            instance_id=item["instance_id"],
            state=item["state"],
            spec=_decimals_to_int(item["spec"]),
            operation_id=item["operation_id"],
            version=int(item["version"]),
            steps=item.get("steps", {}),
            error=item.get("error"),
        )


def _decimals_to_int(value):
    """DynamoDB returns every number as decimal.Decimal via boto3's resource
    layer. Anything that later does a real type check -- socket.create_connection
    on backend_port, for one -- breaks on that, not on a plain int. All our
    numeric spec fields (ports, bandwidth, rate limits) are whole numbers by
    construction (ServiceSpec validates them as int), so int() is always safe
    here. Found by running the reachability check against a real DynamoDB-backed
    tenant, not by a unit test: the test's fake checker never made a real
    socket call, so it never hit this."""
    if isinstance(value, dict):
        return {k: _decimals_to_int(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value)
    return value
