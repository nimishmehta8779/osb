import json
import os
import tempfile
import unittest
import urllib.error

import agent

GOOD = {
    "version_info": "123",
    "resources": [
        {"@type": "type.googleapis.com/envoy.config.listener.v3.Listener", "name": "listener_http"}
    ],
}


def body(doc):
    return json.dumps(doc).encode()


class ValidateTests(unittest.TestCase):
    def test_accepts_good_response(self):
        self.assertEqual(agent.validate(body(GOOD), "listeners")["version_info"], "123")

    def test_rejects_non_json(self):
        with self.assertRaises(agent.InvalidResponse):
            agent.validate(b"<html>502</html>", "listeners")

    def test_rejects_empty_resources(self):
        bad = dict(GOOD, resources=[])
        with self.assertRaises(agent.InvalidResponse):
            agent.validate(body(bad), "listeners")

    def test_rejects_missing_version(self):
        bad = {k: v for k, v in GOOD.items() if k != "version_info"}
        with self.assertRaises(agent.InvalidResponse):
            agent.validate(body(bad), "listeners")

    def test_empty_clusters_is_valid(self):
        # No tenants yet is a real, valid state for clusters, unlike listeners.
        empty = {"version_info": "123", "resources": []}
        doc = agent.validate(body(empty), "clusters")
        self.assertEqual(doc["resources"], [])

    def test_missing_resources_key_is_still_rejected_for_clusters(self):
        bad = {"version_info": "123"}
        with self.assertRaises(agent.InvalidResponse):
            agent.validate(body(bad), "clusters")

    def test_rejects_wrong_type_for_listeners(self):
        bad = json.loads(body(GOOD))
        bad["resources"][0]["@type"] = "type.googleapis.com/envoy.config.cluster.v3.Cluster"
        with self.assertRaises(agent.InvalidResponse):
            agent.validate(body(bad), "listeners")

    def test_rejects_resource_without_name(self):
        bad = json.loads(body(GOOD))
        del bad["resources"][0]["name"]
        with self.assertRaises(agent.InvalidResponse):
            agent.validate(body(bad), "listeners")


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "lds.json")

    def test_first_write_creates_file(self):
        agent.atomic_write(self.path, b"v1")
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), b"v1")
        self.assertFalse(os.path.exists(self.path + ".prev"))

    def test_second_write_keeps_previous_for_rollback(self):
        agent.atomic_write(self.path, b"v1")
        agent.atomic_write(self.path, b"v2")
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), b"v2")
        with open(self.path + ".prev", "rb") as f:
            self.assertEqual(f.read(), b"v1")

    def test_no_temp_files_left_behind(self):
        agent.atomic_write(self.path, b"v1")
        agent.atomic_write(self.path, b"v2")
        self.assertEqual(sorted(os.listdir(self.dir)), ["lds.json", "lds.json.prev"])


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "lds.json")
        self._orig_fetch = agent.fetch

    def tearDown(self):
        agent.fetch = self._orig_fetch

    def test_bad_response_keeps_last_good_file(self):
        agent.atomic_write(self.path, body(GOOD))
        agent.fetch = lambda *a, **k: b"<html>oops</html>"
        with self.assertRaises(agent.InvalidResponse):
            agent.sync_once("http://x", "listeners", self.path, 1)
        with open(self.path, "rb") as f:
            self.assertEqual(json.loads(f.read()), GOOD)

    def test_unreachable_control_plane_keeps_last_good_file(self):
        import urllib.error
        agent.atomic_write(self.path, body(GOOD))

        def down(*a, **k):
            raise urllib.error.URLError("connection refused")

        agent.fetch = down
        with self.assertRaises(urllib.error.URLError):
            agent.sync_once("http://x", "listeners", self.path, 1)
        with open(self.path, "rb") as f:
            self.assertEqual(json.loads(f.read()), GOOD)

    def test_new_version_is_written(self):
        agent.atomic_write(self.path, body(GOOD))
        newer = dict(GOOD, version_info="124")
        agent.fetch = lambda *a, **k: body(newer)
        self.assertEqual(agent.sync_once("http://x", "listeners", self.path, 1), "updated to 124")

    def test_same_version_is_not_rewritten(self):
        agent.atomic_write(self.path, body(GOOD))
        agent.fetch = lambda *a, **k: body(GOOD)
        self.assertEqual(agent.sync_once("http://x", "listeners", self.path, 1), "unchanged")
        self.assertFalse(os.path.exists(self.path + ".prev"))

    def test_forced_sync_rewrites_even_when_unchanged(self):
        # Covers the race this fixes: a listener Envoy rejected (e.g. for a
        # cluster that didn't exist yet) never gets retried if the file is
        # never rewritten again, because Sovereign's content hasn't changed.
        agent.atomic_write(self.path, body(GOOD))
        agent.fetch = lambda *a, **k: body(GOOD)
        result = agent.sync_once("http://x", "listeners", self.path, 1, force=True)
        self.assertIn("reapplied", result)
        self.assertTrue(os.path.exists(self.path + ".prev"))

    def test_forced_sync_on_a_genuinely_new_version_still_reports_updated(self):
        agent.atomic_write(self.path, body(GOOD))
        newer = dict(GOOD, version_info="124")
        agent.fetch = lambda *a, **k: body(newer)
        self.assertEqual(agent.sync_once("http://x", "listeners", self.path, 1, force=True), "updated to 124")


class FetchTests(unittest.TestCase):
    """Sovereign returns HTTP 404, not a 200 with an empty list, when a
    resource renders to zero resources. Found by running the real stack
    (Stage L3), not by the unit tests alone -- they only exercised validate()
    directly and never went through Sovereign's actual HTTP behavior."""

    def _http_error(self, code):
        return urllib.error.HTTPError("http://x", code, "not found", {}, None)

    def setUp(self):
        self._orig_read = agent._read

    def tearDown(self):
        agent._read = self._orig_read

    def test_404_for_clusters_is_a_valid_empty_state(self):
        agent._read = lambda *a, **k: (_ for _ in ()).throw(self._http_error(404))
        body = agent.fetch("http://x", "clusters", 1)
        doc = json.loads(body)
        self.assertEqual(doc["resources"], [])
        agent.validate(body, "clusters")  # must not raise

    def test_404_for_listeners_is_still_an_error(self):
        agent._read = lambda *a, **k: (_ for _ in ()).throw(self._http_error(404))
        with self.assertRaises(urllib.error.HTTPError):
            agent.fetch("http://x", "listeners", 1)

    def test_other_http_errors_for_clusters_still_raise(self):
        agent._read = lambda *a, **k: (_ for _ in ()).throw(self._http_error(500))
        with self.assertRaises(urllib.error.HTTPError):
            agent.fetch("http://x", "clusters", 1)


if __name__ == "__main__":
    unittest.main()
