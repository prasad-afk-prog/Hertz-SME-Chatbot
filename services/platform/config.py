"""Per-environment configuration (M15 §2 secrets/config). Everything comes from
the environment (or a local ``.env``) — no secrets in code. See ``.env.example``.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HFB_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # identity / environment
    environment: str = "local"        # local | dev | staging | prod
    service_name: str = "platform"    # default; create_app() overrides per service

    # observability (M15 §5)
    log_level: str = "INFO"
    log_json: bool = True
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None

    # data stores — consumed by A2+ via clients.py; optional at the skeleton stage
    database_url: str | None = None
    redis_url: str | None = None

    # async / scheduling (A7) — Celery
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    # http
    request_id_header: str = "X-Request-ID"

    # A4 Ingestion API (POA/02)
    ingest_api_key: str | None = None      # portal->API shared secret; None = open (local only)
    ingest_rate_limit_per_min: int = 0     # 0 disables; else per-customer fixed-window limit


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton (cached). Tests can construct ``Settings``
    directly to override values without touching the cache."""
    return Settings()
