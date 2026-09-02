"""A2 Event Store (POA/03) — durable Postgres log + transactional outbox +
Redis-stream handoff, over the ``generator.models.Event`` contract.

    from services.event_pipeline.store import SqlEventStore, OutboxRelay
    from services.event_pipeline.store import RedisStreamPublisher, bootstrap
"""
from __future__ import annotations

from . import bootstrap
from .publisher import (
    CONSUMER_GROUP,
    STREAM,
    InMemoryStreamPublisher,
    RedisStreamPublisher,
    StreamPublisher,
)
from .relay import OutboxRelay
from .store import SqlEventStore
from .tables import event_outbox, events, metadata

__all__ = [
    "SqlEventStore",
    "OutboxRelay",
    "StreamPublisher",
    "RedisStreamPublisher",
    "InMemoryStreamPublisher",
    "STREAM",
    "CONSUMER_GROUP",
    "metadata",
    "events",
    "event_outbox",
    "bootstrap",
]
