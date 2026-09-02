"""A2 Event Store schema (POA/03 §3.1) as SQLAlchemy Core tables.

Two tables: the append-only ``events`` log (source of truth) and the
transactional ``event_outbox`` that guarantees the Redis-stream publish (POA/03
§3.2 — never a direct dual write). JSON columns are real JSONB on Postgres and
plain JSON on SQLite (so the same schema runs in prod and in the test suite).

Production migrations are owned by Alembic; ``bootstrap.create_all`` mirrors this
metadata for local/dev and the SQLite-backed tests.
"""
from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

# JSONB on Postgres, portable JSON elsewhere (SQLite in tests).
_JSON = JSON().with_variant(JSONB(), "postgresql")

events = Table(
    "events",
    metadata,
    Column("event_id", String, primary_key=True),          # client idempotency key
    Column("customer_id", String, nullable=False),
    Column("session_id", String, nullable=False),
    Column("signal_type", String, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("source", String, nullable=False),
    Column("context", _JSON, nullable=False),
    Column("consent", _JSON, nullable=True),
    Column("schema_version", String, nullable=True),
    Index("idx_events_customer_time", "customer_id", "occurred_at"),
    Index("idx_events_session", "session_id"),
    Index("idx_events_signal", "signal_type", "occurred_at"),
    # Prod: monthly RANGE partition on occurred_at for retention/pruning (POA/03 §3.1).
)

event_outbox = Table(
    "event_outbox",
    metadata,
    # bigserial on Postgres; INTEGER (rowid autoincrement) on SQLite for the tests
    Column(
        "id", BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True,
    ),
    Column("event_id", String, ForeignKey("events.event_id"), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("payload", _JSON, nullable=False),
    Index("idx_outbox_unpublished", "published_at"),
)
