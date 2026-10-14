import unittest

from moto import mock_aws

from services.common.tenants import ACTIVE, DELETED, FAILED, ServiceSpec, TenantStore
from services.common.testing import make_tables
from services.worker.app import saga

SPEC = ServiceSpec.validate(
    {"service_id": "svc-1", "hostname": "app1.osb.internal", "backend_host": "10.0.0.5", "backend_port": 8080},
    "osb.internal",
    team_id="team-1",
)


class FakeDns:
    def __init__(self, fail_upsert=False):
        self.records = set()
        self.calls = []
        self.fail_upsert = fail_upsert

    def upsert(self, hostname):
        self.calls.append(("upsert", hostname))
        if self.fail_upsert:
            raise RuntimeError("route53 throttled")
        self.records.add(hostname)

    def delete(self, hostname):
        self.calls.append(("delete", hostname))
        self.records.discard(hostname)


class FakeReachability:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def check(self, host, port):
        self.calls.append((host, port))
        if self.fail:
            raise OSError("connection refused")


@mock_aws
class SagaTests(unittest.TestCase):
    def setUp(self):
        tenants, hostnames = make_tables()
        self.store = TenantStore(tenants, hostnames)
        self.store.create_pending("i-1", SPEC, "op-1")

    def test_happy_path_activates_and_creates_dns(self):
        dns = FakeDns()
        t = saga.provision(self.store, dns, "i-1")
        self.assertEqual(t.state, ACTIVE)
        self.assertIn("app1.osb.internal", dns.records)
        self.assertEqual(t.steps, {"reserve_hostname": "done", "upsert_dns": "done"})

    def test_redelivery_of_active_tenant_does_nothing(self):
        dns = FakeDns()
        saga.provision(self.store, dns, "i-1")
        saga.provision(self.store, dns, "i-1")
        self.assertEqual(dns.calls, [("upsert", "app1.osb.internal")])

    def test_dns_failure_compensates_and_marks_failed(self):
        dns = FakeDns(fail_upsert=True)
        t = saga.provision(self.store, dns, "i-1")
        self.assertEqual(t.state, FAILED)
        self.assertIn("throttled", t.error)
        # The hostname reservation was undone, so another instance can take it.
        self.store.reserve_hostname("app1.osb.internal", "i-2")

    def test_failed_tenant_can_be_retried_after_fix(self):
        dns = FakeDns(fail_upsert=True)
        saga.provision(self.store, dns, "i-1")
        dns.fail_upsert = False
        t = saga.provision(self.store, dns, "i-1")
        self.assertEqual(t.state, ACTIVE)
        self.assertIn("app1.osb.internal", dns.records)

    def test_deprovision_removes_dns_and_hostname(self):
        dns = FakeDns()
        saga.provision(self.store, dns, "i-1")
        t = saga.deprovision(self.store, dns, "i-1")
        self.assertEqual(t.state, DELETED)
        self.assertNotIn("app1.osb.internal", dns.records)
        self.store.reserve_hostname("app1.osb.internal", "i-2")

    def test_deprovision_records_its_own_operation_id(self):
        # Otherwise last_operation keeps reporting the original provision's id.
        dns = FakeDns()
        saga.provision(self.store, dns, "i-1")
        t = saga.deprovision(self.store, dns, "i-1", operation_id="delete-op-1")
        self.assertEqual(t.operation_id, "delete-op-1")

    def test_without_reachability_the_backend_is_never_checked(self):
        # Default behavior (reachability=None) is unchanged from before this feature existed.
        dns = FakeDns()
        t = saga.provision(self.store, dns, "i-1")
        self.assertEqual(t.state, ACTIVE)
        self.assertNotIn("check_backend", t.steps)

    def test_reachable_backend_activates_with_the_check_recorded(self):
        dns = FakeDns()
        reach = FakeReachability()
        t = saga.provision(self.store, dns, "i-1", reachability=reach)
        self.assertEqual(t.state, ACTIVE)
        self.assertEqual(t.steps["check_backend"], "done")
        self.assertEqual(reach.calls, [("10.0.0.5", 8080)])

    def test_unreachable_backend_compensates_and_marks_failed(self):
        dns = FakeDns()
        reach = FakeReachability(fail=True)
        t = saga.provision(self.store, dns, "i-1", reachability=reach)
        self.assertEqual(t.state, FAILED)
        self.assertIn("refused", t.error)
        # DNS and the hostname reservation, both already applied, are undone.
        self.assertNotIn("app1.osb.internal", dns.records)
        self.store.reserve_hostname("app1.osb.internal", "i-2")

    def test_unreachable_backend_can_be_retried_once_it_comes_up(self):
        dns = FakeDns()
        reach = FakeReachability(fail=True)
        saga.provision(self.store, dns, "i-1", reachability=reach)
        reach.fail = False
        t = saga.provision(self.store, dns, "i-1", reachability=reach)
        self.assertEqual(t.state, ACTIVE)


if __name__ == "__main__":
    unittest.main()
