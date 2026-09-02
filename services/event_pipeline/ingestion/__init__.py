"""A4 Event Ingestion API (POA/02) — the single validated write-door into the
pipeline, writing through the A2 Event Store.

    from services.event_pipeline.ingestion import IngestionService, router
"""
from __future__ import annotations

from .auth import (
    AllowAllAuthenticator,
    ApiKeyAuthenticator,
    Principal,
    SourceAuthenticator,
    identity_conflict,
)
from .ratelimit import InMemoryRateLimiter, NoRateLimit, RateLimiter
from .router import router
from .service import IngestionService, IngestOutcome

__all__ = [
    "router",
    "IngestionService",
    "IngestOutcome",
    "Principal",
    "SourceAuthenticator",
    "AllowAllAuthenticator",
    "ApiKeyAuthenticator",
    "identity_conflict",
    "RateLimiter",
    "NoRateLimit",
    "InMemoryRateLimiter",
]
