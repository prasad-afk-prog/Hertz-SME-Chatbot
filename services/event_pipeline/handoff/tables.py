"""Handoff lifecycle ledger (POA/07 §3.4) — audit + M14 handoff-rate + closed-loop
attribution (ticket_ref <-> conversation_id). Portable (Postgres prod / SQLite tests).
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, MetaData, String, Table, func

metadata = MetaData()

handoffs = Table(
    "handoffs",
    metadata,
    Column("id", String, primary_key=True),
    Column("conversation_id", String, nullable=False),
    Column("customer_id", String, nullable=False),
    Column("queue", String, nullable=False),
    Column("ticket_ref", String, nullable=True),         # null when dead-lettered
    Column("rule_id", String, nullable=False),
    Column("reason", String, nullable=False),
    # status: routed | fallback | dead_lettered | accepted | resolved
    Column("status", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("idx_handoffs_status", "status"),
    Index("idx_handoffs_conversation", "conversation_id"),
    Index("idx_handoffs_ticket", "ticket_ref"),
)
