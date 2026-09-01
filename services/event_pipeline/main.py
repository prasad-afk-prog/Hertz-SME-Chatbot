"""Track A — Event & Trigger Pipeline service entrypoint.

Boots the shared platform template and mounts the Track A modules as they land:
A2 Event Store (wired), A4 Ingestion API (wired), then A5 Trigger Eval,
A6 Frequency/Precedence, A7 Pending Queue (Celery), A8 Handoff.

Run locally:  uvicorn services.event_pipeline.main:app --reload
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from services.platform import Settings, create_app, get_logger, get_settings
from services.platform.health import ReadinessCheck

if TYPE_CHECKING:
    from .store import SqlEventStore

log = get_logger("event_pipeline")

MODULES = [
    "A2 event-store", "A4 ingestion", "A5 trigger-eval",
    "A6 frequency-precedence", "A7 pending-queue", "A8 human-handoff",
]


def _redis_ready() -> bool:
    from services.platform.clients import make_redis
    try:
        return bool(make_redis().ping())
    except Exception:
        return False


def build_app(settings: Settings | None = None, *, event_store: SqlEventStore | None = None):
    settings = settings or get_settings()
    checks: dict[str, ReadinessCheck] = {}

    # A2 — Event Store. Built from the configured DB, or injected (tests). Wired
    # only when available, so the app still boots with no infrastructure present.
    if event_store is None and settings.database_url:
        from services.platform.clients import make_engine

        from .store import SqlEventStore, bootstrap

        engine = make_engine(settings)
        if settings.environment in {"local", "dev"}:
            bootstrap.create_all(engine)          # prod schema is owned by Alembic
        event_store = SqlEventStore(engine)

    if event_store is not None:
        checks["postgres"] = event_store.health
    if settings.redis_url:                        # A2 stream conduit / A5 counters
        checks["redis"] = _redis_ready

    app = create_app("event-pipeline", readiness_checks=checks)
    app.state.event_store = event_store

    # A4 — Ingestion API (needs a store to write through).
    if event_store is not None:
        from .ingestion import (
            AllowAllAuthenticator,
            ApiKeyAuthenticator,
            IngestionService,
            InMemoryRateLimiter,
            NoRateLimit,
        )
        from .ingestion import router as ingestion_router

        if settings.ingest_api_key:
            authenticator = ApiKeyAuthenticator(settings.ingest_api_key)
        else:
            authenticator = AllowAllAuthenticator()
            log.warning("ingestion auth is OPEN (no HFB_INGEST_API_KEY set) — dev only")
        rate_limiter = (
            InMemoryRateLimiter(settings.ingest_rate_limit_per_min)
            if settings.ingest_rate_limit_per_min > 0 else NoRateLimit()
        )
        app.state.authenticator = authenticator
        app.state.ingestion_service = IngestionService(event_store, rate_limiter)
        app.include_router(ingestion_router)

    @app.get("/", tags=["meta"])
    def root() -> dict[str, object]:
        return {
            "service": "event-pipeline",
            "track": "A",
            "status": "ingestion+store wired" if event_store else "skeleton",
            "planned_modules": MODULES,
        }

    return app


app = build_app()
