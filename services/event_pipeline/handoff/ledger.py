"""HandoffLedger — records every handoff and tracks its lifecycle (POA/07 §3.4,
§6). The record is the audit trail and the M14 handoff-rate source; agent tooling
advances the status (raised -> routed -> accepted -> resolved) by ticket_ref.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, func, insert, select, update

from .tables import handoffs


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


class HandoffLedger:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def record(
        self, *, id: str, conversation_id: str, customer_id: str, queue: str,
        ticket_ref: str | None, rule_id: str, reason: str, status: str, at: datetime,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(handoffs).values(
                id=id, conversation_id=conversation_id, customer_id=customer_id,
                queue=queue, ticket_ref=ticket_ref, rule_id=rule_id, reason=reason,
                status=status, created_at=_to_utc(at),
            ))

    def update_status(self, ticket_ref: str, status: str) -> bool:
        """Advance lifecycle by ticket_ref (accepted/resolved). Returns whether a
        row moved (unknown/null ticket_ref -> False, surfaced not silent)."""
        with self.engine.begin() as conn:
            result = conn.execute(
                update(handoffs).where(handoffs.c.ticket_ref == ticket_ref)
                .values(status=status, updated_at=datetime.now(UTC))
            )
        return result.rowcount > 0

    def status_of(self, handoff_id: str) -> str | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(handoffs.c.status).where(handoffs.c.id == handoff_id)).first()
        return row[0] if row else None

    def count(self, status: str | None = None) -> int:
        q = select(func.count()).select_from(handoffs)
        if status is not None:
            q = q.where(handoffs.c.status == status)
        with self.engine.connect() as conn:
            return conn.execute(q).scalar_one()
