"""Celery application factory (M15 §3; used by A7 Pending-Engagement Queue).

Celery is imported lazily so the A1 skeleton doesn't require it. A7 adds celery
to the dependencies and defines tasks against the app this returns.
"""
from __future__ import annotations

from typing import Any

from .config import Settings, get_settings


def make_celery(service_name: str, settings: Settings | None = None) -> Any:
    settings = settings or get_settings()
    try:
        from celery import Celery
    except ImportError as exc:  # pragma: no cover - exercised once A7 adds the dep
        raise RuntimeError("celery is not installed; add it for A7 (Pending Queue)") from exc

    broker = settings.celery_broker_url or settings.redis_url
    backend = settings.celery_result_backend or settings.redis_url
    app = Celery(service_name, broker=broker, backend=backend)
    app.conf.update(
        task_track_started=True,
        task_acks_late=True,            # redeliver on worker crash (idempotency principle)
        worker_prefetch_multiplier=1,
        timezone="UTC",
    )
    return app
