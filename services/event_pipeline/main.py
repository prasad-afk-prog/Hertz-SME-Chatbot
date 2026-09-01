"""Track A — Event & Trigger Pipeline service entrypoint.

At the A1 skeleton stage this only proves the shared template boots and is
observable. Subsequent Track A modules mount their routers/workers here:
A2 Event Store, A4 Ingestion API, A5 Trigger Eval, A6 Frequency/Precedence,
A7 Pending Queue (Celery), A8 Human Handoff.

Run locally:  uvicorn services.event_pipeline.main:app --reload
"""
from __future__ import annotations

from services.platform import create_app, get_logger, get_settings
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


def build_app():
    settings = get_settings()
    checks: dict[str, ReadinessCheck] = {}
    if settings.redis_url:          # only assert readiness on configured dependencies
        checks["redis"] = _redis_ready
    # A2 will add a "postgres" check here once database_url is wired.

    app = create_app("event-pipeline", readiness_checks=checks)

    @app.get("/", tags=["meta"])
    def root() -> dict[str, object]:
        return {
            "service": "event-pipeline",
            "track": "A",
            "status": "skeleton",
            "planned_modules": MODULES,
        }

    return app


app = build_app()
