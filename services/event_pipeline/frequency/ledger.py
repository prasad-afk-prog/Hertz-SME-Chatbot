"""EngagementLedger — the Postgres-backed cap ledger (POA/05 §3.1).

Cap accounting reads engagement timestamps from here and hands them to
`reference.would_fire` (the executable sliding-window spec the invariant suite
already asserts against), so the service and the spec can't diverge. Only
`reserved`/`confirmed` rows count; `rolled_back` rows are ignored.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, insert, select, update

from .tables import engagements

_COUNTED = ("reserved", "confirmed")


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


class EngagementLedger:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def reserve(self, reservation_id: str, customer_id: str, trigger_id: str, at: datetime) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(engagements).values(
                reservation_id=reservation_id,
                customer_id=customer_id,
                trigger_id=trigger_id,
                reserved_at=_to_utc(at),
                status="reserved",
            ))

    def set_status(
        self, reservation_id: str, status: str, *, only_from: tuple[str, ...] | None = None
    ) -> bool:
        """Transition a reservation. Returns True if a row actually moved.

        `only_from` guards the transition. Without it an unconditional UPDATE
        lets a late or duplicated `confirm` land on an already `rolled_back`
        row and silently re-burn a slot the customer never received — and a
        call against an unknown reservation_id is a silent no-op rather than a
        surfaced bug. Both are realistic: confirm/rollback come from M08 over a
        network, where retries and out-of-order delivery are normal.
        """
        stmt = (
            update(engagements)
            .where(engagements.c.reservation_id == reservation_id)
            .values(status=status, updated_at=datetime.now(UTC))
        )
        if only_from is not None:
            stmt = stmt.where(engagements.c.status.in_(only_from))
        with self.engine.begin() as conn:
            return conn.execute(stmt).rowcount > 0

    def fire_times(self, customer_id: str, trigger_id: str | None = None) -> list[datetime]:
        """Timestamps of counted (reserved/confirmed) engagements — per trigger,
        or across all triggers when trigger_id is None (the global cap)."""
        q = select(engagements.c.reserved_at).where(
            engagements.c.customer_id == customer_id,
            engagements.c.status.in_(_COUNTED),
        )
        if trigger_id is not None:
            q = q.where(engagements.c.trigger_id == trigger_id)
        with self.engine.connect() as conn:
            return [_to_utc(r[0]) for r in conn.execute(q)]

    def last_engagement_at(self, customer_id: str) -> datetime | None:
        q = (
            select(engagements.c.reserved_at)
            .where(engagements.c.customer_id == customer_id, engagements.c.status.in_(_COUNTED))
            .order_by(engagements.c.reserved_at.desc())
            .limit(1)
        )
        with self.engine.connect() as conn:
            row = conn.execute(q).first()
        return _to_utc(row[0]) if row else None

    def status_of(self, reservation_id: str) -> str | None:
        q = select(engagements.c.status).where(engagements.c.reservation_id == reservation_id)
        with self.engine.connect() as conn:
            row = conn.execute(q).first()
        return row[0] if row else None
