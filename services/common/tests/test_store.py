import unittest

from moto import mock_aws

from services.common.tenants import ACTIVE, Conflict, PENDING, ServiceSpec, TenantStore
from services.common.testing import make_tables

SUFFIX = "osb.internal"
SPEC = ServiceSpec.validate(
    {"service_id": "svc-1", "hostname": "app1.osb.internal", "backend_host": "10.0.0.5", "backend_port": 8080},
    SUFFIX,
    team_id="team-1",
)


class SpecValidationTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(SPEC.hostname, "app1.osb.internal")

    def test_rejects_public_domain(self):
        with self.assertRaises(ValueError):
            ServiceSpec.validate({"service_id": "s", "hostname": "app.example.com", "backend_host": "h", "backend_port": 80}, SUFFIX, team_id="team-1")

    def test_rejects_bad_label(self):
        for bad in ["-bad.osb.internal", "UP_CASE_.osb.internal", "a" * 64 + ".osb.internal"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ServiceSpec.validate({"service_id": "s", "hostname": bad, "backend_host": "h", "backend_port": 80}, SUFFIX, team_id="team-1")

    def test_rejects_bad_port(self):
        with self.assertRaises(ValueError):
            ServiceSpec.validate({"service_id": "s", "hostname": "a.osb.internal", "backend_host": "h", "backend_port": 0}, SUFFIX, team_id="team-1")

    def test_rejects_missing_team_id(self):
        with self.assertRaises(ValueError):
            ServiceSpec.validate({"service_id": "s", "hostname": "a.osb.internal", "backend_host": "h", "backend_port": 80}, SUFFIX)

    def test_bandwidth_and_rate_limit_default_when_not_given(self):
        from services.common.tenants import DEFAULT_BANDWIDTH_KBPS, DEFAULT_RATE_LIMIT_RPS

        self.assertEqual(SPEC.bandwidth_kbps, DEFAULT_BANDWIDTH_KBPS)
        self.assertEqual(SPEC.rate_limit_rps, DEFAULT_RATE_LIMIT_RPS)

    def test_tenant_can_request_a_higher_tier(self):
        spec = ServiceSpec.validate(
            {"service_id": "s", "hostname": "a.osb.internal", "backend_host": "h", "backend_port": 80,
             "bandwidth_kbps": 50_000, "rate_limit_rps": 500},
            SUFFIX,
            team_id="team-1",
        )
        self.assertEqual((spec.bandwidth_kbps, spec.rate_limit_rps), (50_000, 500))

    def test_rejects_tier_above_the_ceiling(self):
        with self.assertRaises(ValueError):
            ServiceSpec.validate(
                {"service_id": "s", "hostname": "a.osb.internal", "backend_host": "h", "backend_port": 80,
                 "bandwidth_kbps": 999_999_999},
                SUFFIX,
                team_id="team-1",
            )

    def test_rejects_non_integer_tier(self):
        for bad in [True, "100", 1.5, -5, 0]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ServiceSpec.validate(
                        {"service_id": "s", "hostname": "a.osb.internal", "backend_host": "h", "backend_port": 80,
                         "rate_limit_rps": bad},
                        SUFFIX,
                        team_id="team-1",
                    )


@mock_aws
class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tenants, self.hostnames = make_tables()
        self.store = TenantStore(self.tenants, self.hostnames)

    def test_numeric_spec_fields_are_plain_ints_after_a_real_round_trip(self):
        # DynamoDB returns every number as decimal.Decimal. A plain-dict fake store
        # would never catch this; this test writes and reads back through the
        # real (moto-mocked) table, which does return Decimal, same as real AWS.
        # Found live: socket.create_connection rejected a Decimal port with
        # "Int or String expected" -- the fake reachability checker in the saga
        # tests never made a real socket call, so it never caught it either.
        self.store.create_pending("i-1", SPEC, "op-1")
        spec = self.store.get("i-1").spec
        for key in ("backend_port", "bandwidth_kbps", "rate_limit_rps"):
            with self.subTest(key=key):
                self.assertIsInstance(spec[key], int)

    def test_create_twice_conflicts(self):
        self.store.create_pending("i-1", SPEC, "op-1")
        with self.assertRaises(Conflict):
            self.store.create_pending("i-1", SPEC, "op-2")

    def test_transition_with_stale_version_conflicts(self):
        t = self.store.create_pending("i-1", SPEC, "op-1")
        self.store.transition(t, ACTIVE)  # bumps version
        with self.assertRaises(Conflict):
            self.store.transition(t, PENDING)  # t is stale

    def test_transition_can_update_operation_id(self):
        t = self.store.create_pending("i-1", SPEC, "op-1")
        t = self.store.transition(t, ACTIVE, operation_id="op-2")
        self.assertEqual(self.store.get("i-1").operation_id, "op-2")

    def test_transition_leaves_operation_id_alone_when_not_given(self):
        t = self.store.create_pending("i-1", SPEC, "op-1")
        self.store.transition(t, ACTIVE)
        self.assertEqual(self.store.get("i-1").operation_id, "op-1")

    def test_hostname_has_one_owner(self):
        self.store.reserve_hostname("app1.osb.internal", "i-1")
        self.store.reserve_hostname("app1.osb.internal", "i-1")  # same owner: fine
        with self.assertRaises(Conflict):
            self.store.reserve_hostname("app1.osb.internal", "i-2")

    def test_release_only_by_owner(self):
        self.store.reserve_hostname("app1.osb.internal", "i-1")
        self.store.release_hostname("app1.osb.internal", "i-2")  # not owner: no-op
        with self.assertRaises(Conflict):
            self.store.reserve_hostname("app1.osb.internal", "i-2")
        self.store.release_hostname("app1.osb.internal", "i-1")
        self.store.reserve_hostname("app1.osb.internal", "i-2")

    def test_active_tenants_excludes_pending(self):
        self.store.create_pending("i-1", SPEC, "op-1")
        t2 = self.store.create_pending("i-2", ServiceSpec.validate({"service_id": "s2", "hostname": "b.osb.internal", "backend_host": "h", "backend_port": 80}, SUFFIX, team_id="team-1"), "op-2")
        self.store.transition(t2, ACTIVE)
        self.assertEqual([t.instance_id for t in self.store.active_tenants()], ["i-2"])


if __name__ == "__main__":
    unittest.main()
