"""Lazy Postgres/Redis client factories (M15 §3).

Imports of SQLAlchemy/redis are deferred to call time so the A1 skeleton needs
neither installed. A2 (Event Store) and A5/A6 (Redis) add them and wire real
connections; until then these raise a clear, actionable error.
"""
from __future__ import annotations

from typing import Any

from .config import Settings, get_settings


def make_engine(settings: Settings | None = None) -> Any:
    """A SQLAlchemy engine for the configured ``database_url`` (A2)."""
    settings = settings or get_settings()
    if not settings.database_url:
        raise RuntimeError("HFB_DATABASE_URL is not configured")
    try:
        from sqlalchemy import create_engine
    except ImportError as exc:  # pragma: no cover - exercised once A2 adds the dep
        raise RuntimeError("sqlalchemy is not installed; add it for A2 (Event Store)") from exc
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


def make_redis(settings: Settings | None = None) -> Any:
    """A Redis client for the configured ``redis_url`` (A2 streams / A5 counters)."""
    settings = settings or get_settings()
    if not settings.redis_url:
        raise RuntimeError("HFB_REDIS_URL is not configured")
    try:
        import redis
    except ImportError as exc:  # pragma: no cover - exercised once A2 adds the dep
        raise RuntimeError("redis is not installed; add it for A2/A5") from exc
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)
