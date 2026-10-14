"""Service Broker: Open Service Broker-style API.

  PUT    /v2/service_instances/{instance_id}              provision (202, async)
  GET    /v2/service_instances/{instance_id}/last_operation  poll
  DELETE /v2/service_instances/{instance_id}              deprovision (202, async)
  GET    /internal/context/tenants                        ACTIVE tenants for Sovereign

Idempotency: instance_id is the key. Same spec again returns the existing state
(200), a different spec returns 409, and a new id returns 202 and enqueues work.
"""

import uuid
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from services.common.api_keys import ApiKeyStore
from services.common.tenants import (
    ACTIVE,
    DELETED,
    DEPROVISIONING,
    FAILED,
    PENDING,
    Conflict,
    NotFound,
    ServiceSpec,
    TenantStore,
)


def create_app(store: TenantStore, queue, *, zone_suffix: str, api_keys: ApiKeyStore) -> FastAPI:
    app = FastAPI(title="OSB service broker", version="0.1.0")

    def require_team(x_api_key: Optional[str] = Header(default=None)) -> str:
        team_id = api_keys.resolve_team(x_api_key or "")
        if team_id is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing or invalid X-Api-Key")
        return team_id

    def require_owning_team(instance_id: str, team_id: str = Depends(require_team)) -> str:
        """For operations on an existing instance: the caller's team must be the
        one that provisioned it. A team can never deprovision or poll another
        team's instance just by guessing its id."""
        try:
            tenant = store.get(instance_id)
        except NotFound:
            return team_id  # nothing to own yet; the endpoint itself handles 404/410
        if tenant.spec.get("team_id") != team_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="instance belongs to another team")
        return team_id

    @app.put("/v2/service_instances/{instance_id}")
    async def provision(instance_id: str, request: Request, team_id: str = Depends(require_owning_team)):
        body = await request.json()
        try:
            spec = ServiceSpec.validate(
                {**body.get("parameters", {}), "service_id": body.get("service_id", "")},
                zone_suffix,
                team_id=team_id,
            )
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))

        try:
            existing = store.get(instance_id)
        except NotFound:
            existing = None

        if existing is not None:
            if existing.spec != spec.as_dict():
                raise HTTPException(status.HTTP_409_CONFLICT, detail="instance exists with different parameters")
            if existing.state == ACTIVE:
                return JSONResponse({"state": ACTIVE}, status_code=200)
            # Still in progress or failed: re-enqueue. The worker is idempotent, so a duplicate is safe.
            queue.send({"op": "provision", "instance_id": instance_id, "operation_id": existing.operation_id})
            return JSONResponse({"operation": existing.operation_id}, status_code=202)

        operation_id = str(uuid.uuid4())
        try:
            store.create_pending(instance_id, spec, operation_id)
        except Conflict:
            # Lost a race with a concurrent identical PUT. Return the winner's state.
            winner = store.get(instance_id)
            if winner.spec != spec.as_dict():
                raise HTTPException(status.HTTP_409_CONFLICT, detail="instance exists with different parameters")
            return JSONResponse({"operation": winner.operation_id}, status_code=202)

        queue.send({"op": "provision", "instance_id": instance_id, "operation_id": operation_id})
        return JSONResponse({"operation": operation_id}, status_code=202)

    @app.get("/v2/service_instances/{instance_id}/last_operation")
    async def last_operation(instance_id: str, team_id: str = Depends(require_owning_team)):
        try:
            tenant = store.get(instance_id)
        except NotFound:
            raise HTTPException(status.HTTP_410_GONE)
        return {"state": _osb_state(tenant.state), "description": tenant.error or tenant.state, "operation": tenant.operation_id}

    @app.delete("/v2/service_instances/{instance_id}")
    async def deprovision(instance_id: str, team_id: str = Depends(require_owning_team)):
        try:
            tenant = store.get(instance_id)
        except NotFound:
            return JSONResponse({}, status_code=200)
        if tenant.state == DELETED:
            return JSONResponse({}, status_code=200)
        operation_id = str(uuid.uuid4())
        queue.send({"op": "deprovision", "instance_id": instance_id, "operation_id": operation_id})
        return JSONResponse({"operation": operation_id}, status_code=202)

    @app.get("/internal/context/tenants")
    async def context_tenants():
        # Read-only, for Sovereign's HTTP context plugin. Reachable inside the VPC only.
        tenants = [
            {
                "instance_id": t.instance_id,
                "hostname": t.hostname,
                "backend_host": t.spec["backend_host"],
                "backend_port": t.spec["backend_port"],
                "bandwidth_kbps": t.spec["bandwidth_kbps"],
                "rate_limit_rps": t.spec["rate_limit_rps"],
            }
            for t in store.active_tenants()
        ]
        return {"tenants": tenants}

    return app


def _osb_state(state: str) -> str:
    if state == ACTIVE or state == DELETED:
        return "succeeded"
    if state == FAILED:
        return "failed"
    if state in (PENDING, DEPROVISIONING):
        return "in progress"
    return "failed"
