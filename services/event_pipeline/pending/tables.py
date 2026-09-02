"""A7 pending-engagement queue schema (POA/06 §3.1).

Deferred matches wait here until the customer's next eligible login within their
validity window. `eligible_at = created + wait_period`, `expires_at = created +
expiry`. The event context for re-eval is fetched from A2 by `event_id` rather
than duplicated here. Portable (Postgres prod / SQLite tests).
"""
from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
    func,
)

metadata = MetaData()

pending_engagements = Table(
    "pending_engagements",
    metadata,
    Column("id", String, primary_key=True),                 # enqueue handle (uuid)
    Column("customer_id", String, nullable=False),
    Column("trigger_id", String, nullable=False),
    Column("event_id", String, nullable=False),             # links back to A2 for context
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("eligible_at", DateTime(timezone=True), nullable=False),   # created + wait_period
    Column("expires_at", DateTime(timezone=True), nullable=False),    # created + expiry
    Column("status", String, nullable=False),               # pending | raising | raised | expired
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("idx_pending_customer_status", "customer_id", "status"),
    Index("idx_pending_expiry", "expires_at"),
)
