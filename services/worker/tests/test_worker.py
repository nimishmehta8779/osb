import json
import unittest

from moto import mock_aws

from services.common.tenants import ACTIVE, ServiceSpec, TenantStore
from services.common.testing import make_tables
from services.worker.app import worker
from services.worker.tests.test_saga import FakeDns, FakeReachability, SPEC


@mock_aws
class WorkerTests(unittest.TestCase):
    def setUp(self):
        tenants, hostnames = make_tables()
        self.store = TenantStore(tenants, hostnames)
        self.store.create_pending("i-1", SPEC, "op-1")
        self.dns = FakeDns()

    def test_handle_provision(self):
        worker.handle({"op": "provision", "instance_id": "i-1", "operation_id": "op-1"}, self.store, self.dns)
        self.assertEqual(self.store.get("i-1").state, ACTIVE)

    def test_handle_passes_reachability_through_to_the_saga(self):
        reach = FakeReachability()
        worker.handle({"op": "provision", "instance_id": "i-1"}, self.store, self.dns, reachability=reach)
        self.assertEqual(self.store.get("i-1").state, ACTIVE)
        self.assertEqual(reach.calls, [("10.0.0.5", 8080)])

    def test_unknown_op_raises_so_message_is_retried(self):
        with self.assertRaises(ValueError):
            worker.handle({"op": "resize", "instance_id": "i-1"}, self.store, self.dns)

    def test_message_deleted_only_after_success(self):
        class _StopTest(BaseException):
            """Not an Exception: run_forever's receive_message guard must not catch this,
            so it still reaches the test and ends the loop."""

        class Sqs:
            def __init__(self, bodies):
                self.bodies = bodies
                self.deleted = []

            def receive_message(self, **kw):
                if not self.bodies:
                    raise _StopTest
                return {"Messages": [{"Body": json.dumps(self.bodies.pop(0)), "ReceiptHandle": "rh", "MessageId": "m"}]}

            def delete_message(self, **kw):
                self.deleted.append(kw["ReceiptHandle"])

        good = {"op": "provision", "instance_id": "i-1"}
        bad = {"op": "bogus", "instance_id": "i-1"}
        sqs = Sqs([bad, good])
        with self.assertRaises(_StopTest):
            worker.run_forever(sqs, "url", self.store, self.dns, wait_seconds=0)
        self.assertEqual(sqs.deleted, ["rh"])  # one delete: the good message only
        self.assertEqual(self.store.get("i-1").state, ACTIVE)

    def test_receive_failure_backs_off_instead_of_crashing(self):
        class FlakySqs:
            def __init__(self):
                self.calls = 0

            def receive_message(self, **kw):
                self.calls += 1
                if self.calls < 3:
                    raise ConnectionError("queue unreachable")
                raise SystemExit  # stop the test once we've proven it survived failures

            def delete_message(self, **kw):
                raise AssertionError("should never be called in this test")

        sqs = FlakySqs()
        with self.assertRaises(SystemExit):
            worker.run_forever(sqs, "url", self.store, self.dns, wait_seconds=0, max_backoff=0.01)
        self.assertEqual(sqs.calls, 3)  # two failures survived, third call reached


if __name__ == "__main__":
    unittest.main()
