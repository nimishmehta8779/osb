"""Provisioning saga for one tenant.

Steps run in order. Each completed step is recorded in the tenant's `steps` map,
so a redelivered message resumes where it stopped instead of repeating work.
If a step fails, completed steps are compensated in reverse order and the tenant
is marked FAILED. A tenant only becomes ACTIVE as the final step, which is what
keeps half-applied tenants out of Sovereign's context.
"""

import logging
from typing import Optional

from services.common.tenants import (
    ACTIVE,
    DELETED,
    DEPROVISIONING,
    FAILED,
    Conflict,
    Tenant,
    TenantStore,
)

log = logging.getLogger("worker.saga")

DONE = "done"


class StepFailed(Exception):
    pass


def _noop(_t):
    pass


def provision(store: TenantStore, dns, instance_id: str, reachability=None) -> Tenant:
    """`reachability`, if given, must have a `.check(host, port)` that raises on
    failure. It's optional so existing callers that don't pass one are
    unaffected; entry.py wires in the real TcpReachability check."""
    tenant = store.get(instance_id)
    if tenant.state == ACTIVE:
        return tenant

    steps = dict(tenant.steps)
    completed: list[str] = [name for name, status in steps.items() if status == DONE]

    def reserve(t):
        store.reserve_hostname(t.hostname, t.instance_id)

    def undo_reserve(t):
        store.release_hostname(t.hostname, t.instance_id)

    def upsert_dns(t):
        dns.upsert(t.hostname)

    def undo_dns(t):
        dns.delete(t.hostname)

    def check_backend(t):
        reachability.check(t.spec["backend_host"], t.spec["backend_port"])

    plan = [
        ("reserve_hostname", reserve, undo_reserve),
        ("upsert_dns", upsert_dns, undo_dns),
    ]
    if reachability is not None:
        # No compensation needed: a reachability check doesn't create anything to undo.
        # Runs last, so a bad backend fails after DNS/hostname are already in place and
        # gets compensated the same as any other step -- never reaches ACTIVE.
        plan.append(("check_backend", check_backend, _noop))

    try:
        for name, do, _undo in plan:
            if name in completed:
                continue
            do(tenant)
            steps[name] = DONE
            tenant = store.transition(tenant, tenant.state, steps=steps)
            completed.append(name)

        return store.transition(tenant, ACTIVE, steps=steps)
    except Exception as exc:
        log.exception("provisioning failed for %s", instance_id)
        _compensate(store, dns, tenant, completed, plan)
        steps = {k: v for k, v in steps.items() if k not in completed}
        try:
            return store.transition(tenant, FAILED, steps=steps, error=str(exc))
        except Conflict:
            return store.get(instance_id)


def _compensate(store: TenantStore, dns, tenant: Tenant, completed: list[str], plan) -> None:
    undo = {name: undo_fn for name, _do, undo_fn in plan}
    for name in reversed(completed):
        try:
            undo[name](tenant)
        except Exception:
            # Compensations are idempotent. Log and keep going, so one failed undo
            # doesn't block the rest. The tenant is still marked FAILED.
            log.exception("compensation %s failed for %s", name, tenant.instance_id)


def deprovision(store: TenantStore, dns, instance_id: str, operation_id: Optional[str] = None) -> Tenant:
    tenant = store.get(instance_id)
    if tenant.state == DELETED:
        return tenant
    # Record the delete's own operation id, so last_operation reports this
    # operation rather than the original provision's.
    tenant = store.transition(tenant, DEPROVISIONING, operation_id=operation_id)
    dns.delete(tenant.hostname)
    store.release_hostname(tenant.hostname, instance_id)
    return store.transition(tenant, DELETED, steps={})
