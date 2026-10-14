"""Render the listener and cluster templates the way Sovereign does, and check
the output the way the LKG agent would: parse it, run it through the same
validate() rules, and confirm the shape is what Envoy expects.

This test needs no running Sovereign, Envoy, or AWS. It's the fast, local proof
that L1 (tenant context -> per-tenant routing) produces something Envoy can load.
"""

import json
import os
import sys
import unittest

import yaml
from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "envoy", "lkg-agent"))
import agent  # noqa: E402

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates", "1.39.2")

TENANTS = {
    "tenants": [
        {"instance_id": "i-1", "hostname": "app1.osb.internal", "backend_host": "10.20.1.5", "backend_port": 8080,
         "bandwidth_kbps": 10_000, "rate_limit_rps": 100},
        {"instance_id": "i-2", "hostname": "app2.osb.internal", "backend_host": "10.20.1.6", "backend_port": 9090,
         "bandwidth_kbps": 50_000, "rate_limit_rps": 500},
    ]
}


def render(name: str, **context) -> dict:
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    text = env.get_template(name).render(**context)
    return yaml.safe_load(text)


def as_lkg_response(doc: dict, version: str = "test-1") -> bytes:
    return json.dumps({"version_info": version, "resources": doc["resources"]}).encode()


class ListenerTemplateTests(unittest.TestCase):
    def test_one_virtual_host_per_tenant_plus_catchall(self):
        doc = render("listeners.yaml.j2", tenants=TENANTS)
        vhosts = doc["resources"][0]["filter_chains"][0]["filters"][0]["typed_config"]["route_config"]["virtual_hosts"]
        names = [v["name"] for v in vhosts]
        self.assertEqual(names, ["tenant_i-1", "tenant_i-2", "osb_default"])
        self.assertEqual(vhosts[0]["domains"], ["app1.osb.internal"])
        self.assertEqual(vhosts[0]["routes"][0]["route"]["cluster"], "tenant_i-1")

    def test_catchall_present_with_zero_tenants(self):
        doc = render("listeners.yaml.j2", tenants={"tenants": []})
        vhosts = doc["resources"][0]["filter_chains"][0]["filters"][0]["typed_config"]["route_config"]["virtual_hosts"]
        self.assertEqual([v["name"] for v in vhosts], ["osb_default"])

    def test_agent_accepts_the_rendered_listener(self):
        doc = render("listeners.yaml.j2", tenants=TENANTS)
        validated = agent.validate(as_lkg_response(doc), "listeners")
        # Two listeners: plain HTTP (10000) and self-signed TLS (10443), same routes.
        self.assertEqual(len(validated["resources"]), 2)

    def test_https_listener_has_a_tls_transport_socket_and_the_same_routes(self):
        doc = render("listeners.yaml.j2", tenants=TENANTS)
        https = next(r for r in doc["resources"] if r["name"] == "listener_https")
        self.assertEqual(https["address"]["socket_address"]["port_value"], 10443)
        chain = https["filter_chains"][0]
        self.assertEqual(chain["transport_socket"]["name"], "envoy.transport_sockets.tls")
        cert = chain["transport_socket"]["typed_config"]["common_tls_context"]["tls_certificates"][0]
        self.assertEqual(cert["certificate_chain"]["filename"], "/etc/envoy/tls/cert.pem")
        vhosts = chain["filters"][0]["typed_config"]["route_config"]["virtual_hosts"]
        self.assertEqual([v["name"] for v in vhosts], ["tenant_i-1", "tenant_i-2", "osb_default"])

    def test_agent_accepts_zero_tenant_listener_too(self):
        # The catch-all keeps the listener non-empty even with no tenants.
        doc = render("listeners.yaml.j2", tenants={"tenants": []})
        agent.validate(as_lkg_response(doc), "listeners")  # must not raise

    def test_each_tenant_route_carries_its_own_rate_and_bandwidth_limit(self):
        doc = render("listeners.yaml.j2", tenants=TENANTS)
        vhosts = doc["resources"][0]["filter_chains"][0]["filters"][0]["typed_config"]["route_config"]["virtual_hosts"]
        cfg = vhosts[0]["routes"][0]["typed_per_filter_config"]
        self.assertEqual(cfg["envoy.filters.http.local_ratelimit"]["token_bucket"]["max_tokens"], 100)
        self.assertEqual(cfg["envoy.filters.http.bandwidth_limit"]["limit_kbps"], 10_000)
        cfg2 = vhosts[1]["routes"][0]["typed_per_filter_config"]
        self.assertEqual(cfg2["envoy.filters.http.local_ratelimit"]["token_bucket"]["max_tokens"], 500)
        self.assertEqual(cfg2["envoy.filters.http.bandwidth_limit"]["limit_kbps"], 50_000)

    def test_catchall_route_has_no_per_tenant_override(self):
        # It falls back to the filter chain's own generous defaults.
        doc = render("listeners.yaml.j2", tenants=TENANTS)
        vhosts = doc["resources"][0]["filter_chains"][0]["filters"][0]["typed_config"]["route_config"]["virtual_hosts"]
        catchall = next(v for v in vhosts if v["name"] == "osb_default")
        self.assertNotIn("typed_per_filter_config", catchall["routes"][0])


class ClusterTemplateTests(unittest.TestCase):
    def test_one_cluster_per_tenant_with_its_own_backend(self):
        doc = render("clusters.yaml.j2", tenants=TENANTS)
        clusters = {c["name"]: c for c in doc["resources"]}
        self.assertEqual(set(clusters), {"tenant_i-1", "tenant_i-2"})
        addr = clusters["tenant_i-2"]["load_assignment"]["endpoints"][0]["lb_endpoints"][0]["endpoint"]["address"]["socket_address"]
        self.assertEqual((addr["address"], addr["port_value"]), ("10.20.1.6", 9090))

    def test_agent_accepts_the_rendered_clusters(self):
        doc = render("clusters.yaml.j2", tenants=TENANTS)
        validated = agent.validate(as_lkg_response(doc), "clusters")
        self.assertEqual(len(validated["resources"]), 2)

    def test_zero_tenants_is_an_empty_but_valid_cluster_list(self):
        doc = render("clusters.yaml.j2", tenants={"tenants": []})
        self.assertEqual(doc["resources"], [])
        agent.validate(as_lkg_response(doc), "clusters")  # must not raise

    def test_circuit_breaker_is_derived_from_each_tenant_own_rate_limit(self):
        doc = render("clusters.yaml.j2", tenants=TENANTS)
        clusters = {c["name"]: c for c in doc["resources"]}
        t1 = clusters["tenant_i-1"]["circuit_breakers"]["thresholds"][0]
        self.assertEqual((t1["max_connections"], t1["max_requests"]), (100, 200))
        t2 = clusters["tenant_i-2"]["circuit_breakers"]["thresholds"][0]
        self.assertEqual((t2["max_connections"], t2["max_requests"]), (500, 1000))
        # One tenant's circuit breaker is independent of another's.
        self.assertNotEqual(t1["max_connections"], t2["max_connections"])


if __name__ == "__main__":
    unittest.main()
