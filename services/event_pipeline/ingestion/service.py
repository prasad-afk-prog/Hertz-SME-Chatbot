"""Ingestion logic (POA/02 §3.3), independent of HTTP so it's unit-testable.

Per event: identity bind -> rate limit -> write-through to the Event Store (A2),
which persists to Postgres and enqueues the transactional outbox; the relay
publishes to the stream. Idempotency is the store's (dedupe on event_id), so a
client retry is safe. The batch path validates each item independently for
partial success.
"""
from __future__ import annotations

from enum import Enum

from pydantic import ValidationError

from generator.models import Event

from ..store import SqlEventStore
from .auth import Principal, identity_conflict
from .ratelimit import NoRateLimit, RateLimiter
from .schemas import BatchItemResult


class IngestOutcome(str, Enum):
    accepted = "accepted"
    duplicate = "duplicate"
    rate_limited = "rate_limited"
    identity_conflict = "identity_conflict"


class IngestionService:
    def __init__(self, store: SqlEventStore, rate_limiter: RateLimiter | None = None) -> None:
        self.store = store
        self.rate_limiter = rate_limiter or NoRateLimit()

    def ingest(self, event: Event, principal: Principal) -> IngestOutcome:
        if identity_conflict(principal, event.customer_id):
            return IngestOutcome.identity_conflict
        if not self.rate_limiter.allow(principal.source, event.customer_id):
            return IngestOutcome.rate_limited
        # write_event is idempotent and enqueues the outbox; it may raise if the
        # store is down — the caller maps that to 503.
        newly = self.store.write_event(event)
        return IngestOutcome.accepted if newly else IngestOutcome.duplicate

    def ingest_batch(self, raw_items: list[dict], principal: Principal) -> list[BatchItemResult]:
        results: list[BatchItemResult] = []
        for i, raw in enumerate(raw_items):
            eid = raw.get("event_id") if isinstance(raw, dict) else None
            try:
                event = Event.model_validate(raw)
            except ValidationError as exc:
                results.append(BatchItemResult(
                    index=i, event_id=eid, status="invalid",
                    detail=f"{exc.error_count()} validation error(s)",
                ))
                continue
            outcome = self.ingest(event, principal)
            results.append(BatchItemResult(index=i, event_id=event.event_id, status=outcome.value))
        return results
