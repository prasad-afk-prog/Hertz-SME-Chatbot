"""PendingQueue (A7 / M06) — the deferred-engagement store (POA/06 §3).

Implements A5's DeferredSink (``enqueue(item)``), so A5 can write deferred matches
straight in. Eligibility/expiry are computed from the item's wait_period/expiry.
All reads take an explicit ``now`` so eligibility and expiry are time-travel
testable. Only `reserved`-free bookkeeping here — arbitration is A6's job
(see scheduler.py).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine, func, insert, select, update

from generator.durations import parse_duration

from ..triggers.sinks import DeferredItem
from .tables import pending_engagements as pe


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass
class PendingEntry:
    id: str
    customer_id: str
    trigger_id: str
    event_id: str
    created_at: datetime
    eligible_at: datetime
    expires_at: datetime
    status: str


class PendingQueue:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # --- enqueue (A5 DeferredSink) --------------------------------------- #
    def enqueue(self, item: DeferredItem) -> str:
        created = _to_utc(item.occurred_at)
        entry_id = uuid.uuid4().hex
        with self.engine.begin() as conn:
            conn.execute(insert(pe).values(
                id=entry_id,
                customer_id=item.customer_id,
                trigger_id=item.trigger_id,
                event_id=item.event_id,
                created_at=created,
                eligible_at=created + parse_duration(item.wait_period),
                expires_at=created + parse_duration(item.expiry),
                status="pending",
            ))
        return entry_id

    # --- reads ----------------------------------------------------------- #
    @staticmethod
    def _entry(m) -> PendingEntry:
        return PendingEntry(
            id=m["id"], customer_id=m["customer_id"], trigger_id=m["trigger_id"],
            event_id=m["event_id"], created_at=_to_utc(m["created_at"]),
            eligible_at=_to_utc(m["eligible_at"]), expires_at=_to_utc(m["expires_at"]),
            status=m["status"],
        )

    def eligible_pending(self, customer_id: str, now: datetime) -> list[PendingEntry]:
        """Pending entries whose wait has elapsed and window hasn't expired."""
        now = _to_utc(now)
        q = select(pe).where(
            pe.c.customer_id == customer_id,
            pe.c.status == "pending",
            pe.c.eligible_at <= now,
            pe.c.expires_at > now,
        ).order_by(pe.c.created_at)
        with self.engine.connect() as conn:
            return [self._entry(m) for m in conn.execute(q).mappings()]

    def status_of(self, entry_id: str) -> str | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(pe.c.status).where(pe.c.id == entry_id)).first()
        return row[0] if row else None

    def count(self, customer_id: str | None = None, status: str | None = None) -> int:
        q = select(func.count()).select_from(pe)
        if customer_id is not None:
            q = q.where(pe.c.customer_id == customer_id)
        if status is not None:
            q = q.where(pe.c.status == status)
        with self.engine.connect() as conn:
            return conn.execute(q).scalar_one()

    # --- writes ---------------------------------------------------------- #
    def set_status(self, entry_id: str, status: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(pe).where(pe.c.id == entry_id)
                .values(status=status, updated_at=datetime.now(UTC))
            )

    def expire_due(self, now: datetime) -> list[str]:
        """Expiry sweep (R): mark overdue pending entries expired; return their ids
        (for Z2 logging). Never touches raised/raising rows."""
        now = _to_utc(now)
        with self.engine.begin() as conn:
            due = [r[0] for r in conn.execute(
                select(pe.c.id).where(pe.c.status == "pending", pe.c.expires_at <= now)
            )]
            if due:
                conn.execute(
                    update(pe).where(pe.c.id.in_(due))
                    .values(status="expired", updated_at=datetime.now(UTC))
                )
        return due

    def reconcile_stuck(self, now: datetime, older_than_seconds: int = 300) -> list[str]:
        """Recover rows stuck in `raising` (a crash between claim and raise):
        release them back to pending so a later login can retry (POA/06 §5.6)."""
        cutoff = _to_utc(now).timestamp() - older_than_seconds
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(pe.c.id, pe.c.updated_at).where(pe.c.status == "raising")
            )
            stuck = [r[0] for r in rows if _to_utc(r[1]).timestamp() < cutoff]
            if stuck:
                conn.execute(
                    update(pe).where(pe.c.id.in_(stuck))
                    .values(status="pending", updated_at=datetime.now(UTC))
                )
        return stuck
