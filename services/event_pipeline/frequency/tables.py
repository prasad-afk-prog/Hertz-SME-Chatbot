"""A6 engagement ledger (POA/05 §3.1) — the authoritative record of engagements.

Postgres is the source of truth (Redis counters are a rebuildable cache, deferred);
cap windows are computed from this ledger. A `rolled_back` row does not count
toward a cap, so a failed M08 delivery never burns the customer's slot.
Portable (JSONB-free): runs on Postgres in prod and SQLite in the tests.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    func,
)

metadata = MetaData()

engagements = Table(
    "engagements",
    metadata,
    Column(
        "id", BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True,
    ),
    Column("reservation_id", String, nullable=False, unique=True),   # confirm/rollback handle
    Column("customer_id", String, nullable=False),
    Column("trigger_id", String, nullable=False),
    Column("reserved_at", DateTime(timezone=True), nullable=False),
    # status: reserved | confirmed | rolled_back
    Column("status", String, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("idx_engagements_customer_time", "customer_id", "reserved_at"),
    Index("idx_engagements_customer_trigger", "customer_id", "trigger_id"),
)
