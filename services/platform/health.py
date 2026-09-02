"""Liveness/readiness endpoints (M15 §7).

``/healthz`` is liveness (the process is up). ``/readyz`` runs the service's
registered readiness checks (e.g. DB/Redis reachable) and returns 503 until they
all pass, so orchestrators don't route traffic to a not-yet-ready instance.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable

from fastapi import APIRouter
from starlette.responses import JSONResponse

# A readiness check returns truthy when its dependency is healthy; may be async.
ReadinessCheck = Callable[[], object]


def health_router(
    service_name: str, readiness_checks: dict[str, ReadinessCheck] | None = None
) -> APIRouter:
    router = APIRouter(tags=["health"])
    checks = dict(readiness_checks or {})

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": service_name}

    @router.get("/readyz")
    async def readyz() -> JSONResponse:
        results: dict[str, bool] = {}
        all_ok = True
        for name, check in checks.items():
            try:
                res = check()
                if inspect.isawaitable(res):
                    res = await res
                results[name] = bool(res)
            except Exception:
                results[name] = False
            all_ok = all_ok and results[name]
        return JSONResponse(
            {
                "status": "ready" if all_ok else "not_ready",
                "service": service_name,
                "checks": results,
            },
            status_code=200 if all_ok else 503,
        )

    return router
