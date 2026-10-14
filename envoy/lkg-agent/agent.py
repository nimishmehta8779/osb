"""Last-known-good xDS agent.

Polls Sovereign for listeners and writes a validated response to a local file.
Envoy reads that file through path_config_source, so a restart keeps serving the
last good config even when Sovereign is unreachable.

Only validated responses reach the file. Writes are atomic (temp file + rename),
and the previous file is kept as <file>.prev for rollback.
Stdlib only, so it runs on a stock AMI without extra packages.
"""

import argparse
import json
import logging
import os
import random
import tempfile
import time
import urllib.error
import urllib.request

log = logging.getLogger("lkg-agent")

ENVOY_BUILD_VERSION = "lkg/1.39.2/Clean/RELEASE"
TYPE_PREFIX = "type.googleapis.com/"


class InvalidResponse(Exception):
    pass


def validate(body: bytes, resource_type: str) -> dict:
    """Reject anything Envoy should not load. Schema-level checks only;
    Envoy's own validation runs on the file before it is promoted."""
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise InvalidResponse(f"not JSON: {e}") from e

    if not isinstance(doc, dict):
        raise InvalidResponse("response is not an object")
    version = doc.get("version_info")
    if not isinstance(version, str) or not version:
        raise InvalidResponse("missing version_info")
    resources = doc.get("resources")
    if not isinstance(resources, list):
        raise InvalidResponse("resources missing")
    # An empty listener would leave Envoy answering nothing, so it's rejected.
    # An empty cluster list is valid: it just means no tenants exist yet.
    if not resources and resource_type != "clusters":
        raise InvalidResponse("resources empty")

    expected_prefix = f"{TYPE_PREFIX}envoy.config.{resource_type[:-1]}.v3."
    for r in resources:
        if not isinstance(r, dict):
            raise InvalidResponse("resource is not an object")
        t = r.get("@type", "")
        if not t.startswith(expected_prefix):
            raise InvalidResponse(f"unexpected @type {t!r} for {resource_type}")
        if not isinstance(r.get("name"), str) or not r["name"]:
            raise InvalidResponse("resource without name")
    return doc


def atomic_write(path: str, data: bytes) -> None:
    """Write via temp file in the same directory, then rename.
    Readers see either the old or the new file, never a partial one."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".lkg-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # mkstemp creates 0600. The Envoy container must be able to read the file.
        os.chmod(tmp, 0o644)
        if os.path.exists(path):
            os.replace(path, path + ".prev")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# Sovereign returns HTTP 404, not a 200 with an empty list, when a resource type
# renders to zero resources. For "clusters" that's a real, valid state (no
# tenants yet); for "listeners" it never happens, because the template always
# has a catch-all, so a 404 there is a genuine problem, not treated specially.
EMPTY_RESOURCES_OK_FOR = {"clusters"}
_EMPTY_VERSION = "empty"


def fetch(base_url: str, resource_type: str, timeout: float) -> bytes:
    node = {"node": {"cluster": "osb-dev", "build_version": ENVOY_BUILD_VERSION}, "resource_names": [], "version_info": "0"}
    req = urllib.request.Request(
        f"{base_url}/v3/discovery:{resource_type}",
        data=json.dumps(node).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        return _read(req, timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404 and resource_type in EMPTY_RESOURCES_OK_FOR:
            return json.dumps({"version_info": _EMPTY_VERSION, "resources": []}).encode()
        raise


def _read(req, timeout: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def current_version(path: str):
    try:
        with open(path, "rb") as f:
            return json.loads(f.read()).get("version_info")
    except (OSError, ValueError):
        return None


def sync_once(base_url, resource_type, out_path, timeout, force: bool = False) -> str:
    """`force=True` rewrites the file even if Sovereign's content is
    unchanged. Needed because the listeners and clusters agents poll
    independently: a listener referencing a cluster that doesn't exist yet
    gets rejected by Envoy, and if the listeners response never changes
    again, the agent would otherwise never rewrite the file -- so Envoy would
    never get a reason to retry loading it, even after the cluster shows up
    moments later. Found live, 2026-10-14: a newly provisioned tenant reported
    ACTIVE and was visible in Sovereign/clusters, but never actually routed,
    with no error anywhere, until Envoy was restarted by hand."""
    body = fetch(base_url, resource_type, timeout)
    doc = validate(body, resource_type)
    unchanged = doc["version_info"] == current_version(out_path)
    if unchanged and not force:
        return "unchanged"
    atomic_write(out_path, body)
    if unchanged:
        return f"reapplied {doc['version_info']} (periodic retry)"
    return f"updated to {doc['version_info']}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sovereign", required=True, help="base URL, for example http://sovereign:8080")
    p.add_argument("--type", default="listeners")
    p.add_argument("--out", required=True, help="file Envoy reads via path_config_source")
    p.add_argument("--interval", type=float, default=15.0)
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--force-interval", type=int, default=4,
                    help="force a rewrite every N polls even if unchanged, so a listener Envoy "
                         "previously rejected (e.g. for a not-yet-existing cluster) gets retried "
                         "within N * --interval seconds instead of staying stuck indefinitely")
    p.add_argument("--once", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    failures = 0
    poll_count = 0
    while True:
        poll_count += 1
        force = args.force_interval > 0 and poll_count % args.force_interval == 0
        try:
            log.info("sync: %s", sync_once(args.sovereign, args.type, args.out, args.timeout, force=force))
            failures = 0
            delay = args.interval
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            failures += 1
            log.warning("control plane unreachable (%d): %s; keeping last-known-good", failures, e)
            delay = min(args.interval * 2 ** failures, 300) * random.uniform(0.5, 1.0)
        except InvalidResponse as e:
            failures += 1
            log.error("rejected response: %s; keeping last-known-good", e)
            delay = args.interval
        if args.once:
            return
        time.sleep(delay)


if __name__ == "__main__":
    main()
