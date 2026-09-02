"""The shared service template (M15 §2/§6/§7).

``create_app(service_name)`` returns a FastAPI app with logging, correlation-id
middleware, Prometheus metrics, OpenTelemetry seam, health/readiness endpoints
and consistent error handling already wired in — so a new module is scaffolded
in one line and gets observability for free (POA/15 §7 acceptance criterion).
"""
from __future__ import annotations

from fastapi import FastAPI

from .config import Settings, get_settings
from .errors import register_error_handlers
from .health import ReadinessCheck, health_router
from .logging import configure_logging, get_logger
from .metrics import metrics_endpoint
from .middleware import CorrelationIdMiddleware
from .tracing import configure_tracing


def create_app(
    service_name: str,
    *,
    settings: Settings | None = None,
    readiness_checks: dict[str, ReadinessCheck] | None = None,
    **fastapi_kwargs: object,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    log = get_logger(f"platform.{service_name}")

    app = FastAPI(title=f"HFB {service_name}", **fastapi_kwargs)
    app.state.settings = settings
    app.state.service_name = service_name

    app.add_middleware(
        CorrelationIdMiddleware, service_name=service_name, header=settings.request_id_header
    )
    register_error_handlers(app)
    app.include_router(health_router(service_name, readiness_checks))
    app.add_api_route("/metrics", metrics_endpoint, methods=["GET"], include_in_schema=False)

    tracing_on = configure_tracing(app, settings, service_name)
    log.info(
        "service_initialised",
        extra={"extra_fields": {
            "service": service_name,
            "environment": settings.environment,
            "tracing": tracing_on,
        }},
    )
    return app
