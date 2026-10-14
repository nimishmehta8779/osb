"""Per-team API keys: replaces the broker's single shared Basic-Auth password.

A key is never stored in plaintext -- only its SHA-256 hash, as the DynamoDB
partition key. Lookup is a single GetItem on that hash; there is no key
enumeration or listing path on purpose.

This is deliberately simple (no expiry, no rotation, no per-key scopes beyond
the owning team_id) -- see docs/architecture.md for architectural invariants.
"""

import hashlib
from dataclasses import dataclass
from typing import Optional


def hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


@dataclass
class ApiKeyStore:
    table: "object"  # boto3 Table resource; a dict-backed fake in tests

    def resolve_team(self, api_key: str) -> Optional[str]:
        if not api_key:
            return None
        resp = self.table.get_item(Key={"api_key_hash": hash_key(api_key)}, ConsistentRead=True)
        item = resp.get("Item")
        if item is None or not item.get("active", True):
            return None
        return item["team_id"]
