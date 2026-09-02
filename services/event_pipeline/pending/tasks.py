"""Celery Beat wiring for the pending queue (POA/06 §3.2, §5.3/§5.6).

No top-level celery import — the worker passes its Celery app (made via
services.platform.make_celery) at registration time, so this module imports fine
in the API/test process. The task bodies are thin wrappers over the pure,
directly-tested PendingQueue methods.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .queue import PendingQueue


def beat_schedule(expire_every_seconds: int = 300) -> dict[str, dict]:
    return {
        "pending-expire-sweep": {
            "task": "pending.expire", "schedule": expire_every_seconds,
        },
        "pending-reconcile-stuck": {
            "task": "pending.reconcile", "schedule": expire_every_seconds * 2,
        },
    }


def register_pending_tasks(
    celery_app: Any,
    queue: PendingQueue,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    @celery_app.task(name="pending.expire")
    def expire_pending() -> list[str]:
        return queue.expire_due(now())

    @celery_app.task(name="pending.reconcile")
    def reconcile_stuck() -> list[str]:
        return queue.reconcile_stuck(now())

    return {"expire": expire_pending, "reconcile": reconcile_stuck}
