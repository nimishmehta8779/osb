"""Checks that a tenant's backend actually answers before the worker marks it
ACTIVE. Closes a real gap: without this, a typo'd or unreachable backend_host
would still go live, and only fail once a customer's request hit it.
"""

import socket


class TcpReachability:
    def __init__(self, timeout: float = 3.0):
        self.timeout = timeout

    def check(self, host: str, port: int) -> None:
        """Raises OSError (ConnectionRefusedError, timeout, etc.) if nothing answers."""
        with socket.create_connection((host, port), timeout=self.timeout):
            pass
