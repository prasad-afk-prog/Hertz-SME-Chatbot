"""Track A — Event & Trigger Pipeline service entrypoint.

Boots the shared platform template and, from A2 on, wires the Event Store.
Subsequent Track A modules mount their routers/workers here: A4 Ingestion API,
A5 Trigger Eval, A6 Frequency/Precedence, A7 Pending Queue (Celery), A8 Handoff.

Run locally:  uvicorn services.event_pipeline.main:app --reload
"""
from __future__ import annotations

from services.platform import Settings, create_app, get_logger, get_settings
from services.platform.health import ReadinessCheck

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


def build_app(settings: Settings | None = None):
    settings = settings or get_settings()
    checks: dict[str, ReadinessCheck] = {}
    event_store = None

    # A2 — Event Store. Wired only when a database is configured, so the app
    # still boots (and the tests run) with no infrastructure present.
    if settings.database_url:
        from services.platform.clients import make_engine

        from .store import SqlEventStore, bootstrap

        engine = make_engine(settings)
        if settings.environment in {"local", "dev"}:
            bootstrap.create_all(engine)          # prod schema is owned by Alembic
        event_store = SqlEventStore(engine)
        checks["postgres"] = event_store.health

    if settings.redis_url:                        # A2 stream conduit / A5 counters
        checks["redis"] = _redis_ready

    app = create_app("event-pipeline", readiness_checks=checks)
    app.state.event_store = event_store           # A4 will write through this

    @app.get("/", tags=["meta"])
    def root() -> dict[str, object]:
        return {
            "service": "event-pipeline",
            "track": "A",
            "status": "event-store wired" if event_store else "skeleton",
            "planned_modules": MODULES,
        }

    return app


app = build_app()
