"""No-op DNS adapter for local runs (Stage L3).

Route 53 has no good local stand-in, so the local stack records calls instead
of making them -- the same approach the saga's unit tests already use, just
reachable from a running process instead of a test file. Never used in
Stage D: the worker's entry.py defaults to the real Route 53 adapter, and this
is selected only by an explicit DNS_ADAPTER=noop.
"""

import logging

log = logging.getLogger("worker.dns_noop")


class NoopDns:
    def __init__(self):
        self.records: set[str] = set()

    def upsert(self, hostname: str) -> None:
        log.info("DNS upsert (no-op): %s", hostname)
        self.records.add(hostname)

    def delete(self, hostname: str) -> None:
        log.info("DNS delete (no-op): %s", hostname)
        self.records.discard(hostname)
