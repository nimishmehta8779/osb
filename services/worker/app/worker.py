"""SQS consumer for provisioning messages.

A message is deleted only after the saga finishes (ACTIVE or FAILED). If the saga
crashes, the message is left on the queue and redelivered. After maxReceiveCount
receives, SQS moves it to the dead-letter queue, where the redrive runbook applies.
"""

import json
import logging
import random
import time

from services.worker.app import saga

log = logging.getLogger("worker")


def handle(message: dict, store, dns, reachability=None) -> None:
    op = message["op"]
    iid = message["instance_id"]
    if op == "provision":
        saga.provision(store, dns, iid, reachability=reachability)
    elif op == "deprovision":
        saga.deprovision(store, dns, iid, operation_id=message.get("operation_id"))
    else:
        raise ValueError(f"unknown op {op!r}")


def run_forever(
    sqs, queue_url: str, store, dns, *, reachability=None, wait_seconds: int = 20, max_backoff: float = 60.0
) -> None:
    """Runs until the process is stopped. A down queue doesn't crash the worker:
    it's logged and retried with backoff, the same way the LKG agent handles a
    down control plane. Only a receive failure backs off; a processing failure
    goes through `handle`'s own exception path below, on the next message."""
    failures = 0
    while True:
        try:
            resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=5, WaitTimeSeconds=wait_seconds)
        except Exception:
            failures += 1
            delay = min(2**failures, max_backoff) * random.uniform(0.5, 1.0)
            log.exception("queue unreachable (attempt %d); retrying in %.0fs", failures, delay)
            time.sleep(delay)
            continue
        failures = 0

        for m in resp.get("Messages", []):
            try:
                handle(json.loads(m["Body"]), store, dns, reachability=reachability)
            except Exception:
                # Leave the message in the queue. It is retried, then dead-lettered.
                log.exception("message %s failed; will retry", m.get("MessageId"))
                continue
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
