import unittest

from fastapi.testclient import TestClient
from moto import mock_aws

from services.broker.app.main import create_app
from services.common.api_keys import ApiKeyStore, hash_key
from services.common.tenants import TenantStore
from services.common.testing import make_tables

TEAM_A_KEY, TEAM_A = "key-for-team-a", "team-a"
TEAM_B_KEY, TEAM_B = "key-for-team-b", "team-b"
AUTH_A = {"X-Api-Key": TEAM_A_KEY}
AUTH_B = {"X-Api-Key": TEAM_B_KEY}
BODY = {
    "service_id": "svc-1",
    "parameters": {"hostname": "app1.osb.internal", "backend_host": "10.0.0.5", "backend_port": 8080},
}


class FakeQueue:
    def __init__(self):
        self.messages = []

    def send(self, msg):
        self.messages.append(msg)


class FakeApiKeyTable:
    """dict-backed stand-in for the DynamoDB api_keys table, keyed by hash like the real one."""

    def __init__(self, keys: dict):
        self._items = {hash_key(k): {"api_key_hash": hash_key(k), "team_id": team} for k, team in keys.items()}

    def get_item(self, Key, ConsistentRead=True):
        item = self._items.get(Key["api_key_hash"])
        return {"Item": item} if item else {}


@mock_aws
class BrokerApiTests(unittest.TestCase):
    def setUp(self):
        tenants, hostnames = make_tables()
        self.queue = FakeQueue()
        api_keys = ApiKeyStore(FakeApiKeyTable({TEAM_A_KEY: TEAM_A, TEAM_B_KEY: TEAM_B}))
        app = create_app(
            TenantStore(tenants, hostnames), self.queue,
            zone_suffix="osb.internal", api_keys=api_keys,
        )
        self.client = TestClient(app)

    def put(self, iid, body=BODY, headers=AUTH_A):
        return self.client.put(f"/v2/service_instances/{iid}", json=body, headers=headers)

    def test_requires_auth(self):
        self.assertEqual(self.put("i-1", headers={}).status_code, 401)
        self.assertEqual(self.put("i-1", headers={"X-Api-Key": "not-a-real-key"}).status_code, 401)

    def test_new_instance_is_accepted_and_enqueued(self):
        r = self.put("i-1")
        self.assertEqual(r.status_code, 202)
        self.assertIn("operation", r.json())
        self.assertEqual(self.queue.messages[0]["op"], "provision")

    def test_provisioned_instance_is_tagged_with_the_caller_team(self):
        self.put("i-1", headers=AUTH_A)
        # A different team polling the same id is forbidden, proving team_id stuck.
        r = self.client.get("/v2/service_instances/i-1/last_operation", headers=AUTH_B)
        self.assertEqual(r.status_code, 403)

    def test_same_spec_again_is_idempotent(self):
        first = self.put("i-1").json()["operation"]
        r = self.put("i-1")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()["operation"], first)  # same operation, not a new one

    def test_different_spec_is_conflict(self):
        self.put("i-1")
        other = {**BODY, "parameters": {**BODY["parameters"], "backend_port": 9090}}
        self.assertEqual(self.put("i-1", body=other).status_code, 409)

    def test_invalid_hostname_is_rejected_before_any_work(self):
        bad = {**BODY, "parameters": {**BODY["parameters"], "hostname": "app.example.com"}}
        self.assertEqual(self.put("i-1", body=bad).status_code, 400)
        self.assertEqual(self.queue.messages, [])

    def test_last_operation_reports_pending(self):
        self.put("i-1")
        r = self.client.get("/v2/service_instances/i-1/last_operation", headers=AUTH_A)
        self.assertEqual(r.json()["state"], "in progress")

    def test_another_team_cannot_poll_or_deprovision_your_instance(self):
        self.put("i-1", headers=AUTH_A)
        self.assertEqual(self.client.get("/v2/service_instances/i-1/last_operation", headers=AUTH_B).status_code, 403)
        self.assertEqual(self.client.delete("/v2/service_instances/i-1", headers=AUTH_B).status_code, 403)

    def test_deprovision_is_async(self):
        self.put("i-1")
        r = self.client.delete("/v2/service_instances/i-1", headers=AUTH_A)
        self.assertEqual(r.status_code, 202)
        self.assertEqual(self.queue.messages[-1]["op"], "deprovision")


if __name__ == "__main__":
    unittest.main()
