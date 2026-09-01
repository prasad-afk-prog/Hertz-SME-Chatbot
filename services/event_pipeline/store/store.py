"""A2 Event Store (POA/03) — durable Postgres persistence + the transactional
outbox that guarantees the Redis-stream handoff.

The wire contract is ``generator.models.Event`` (design principle P3): the store
persists and reconstructs that model, so anything the generator emits round-trips
by construction. Runs on Postgres in prod and in-memory SQLite in the tests.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from generator.models import Consent, Event, EventContext

from .tables import event_outbox, events


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


class SqlEventStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # --- write + publish primitive (POA/03 §3.2, used by A4/M02) ---------- #
    def write_event(self, event: Event) -> bool:
        """Persist an event and enqueue it on the outbox in ONE transaction.

        Returns True if newly stored, False if the ``event_id`` was already
        present (idempotent — retries/duplicates are safe). The event lands in
        both ``events`` and ``event_outbox`` or in neither; the stream publish is
        a separate step (the relay), never a direct dual write.
        """
        row = {
            "event_id": event.event_id,
            "customer_id": event.customer_id,
            "session_id": event.session_id,
            "signal_type": event.signal_type.value,
            "occurred_at": _to_utc(event.occurred_at),
            "source": event.source.value,
            "context": event.context.model_dump(mode="json"),
            "consent": event.consent.model_dump(mode="json") if event.consent else None,
            "schema_version": event.schema_version,
        }
        try:
            with self.engine.begin() as conn:
                exists = conn.execute(
                    select(events.c.event_id).where(events.c.event_id == event.event_id)
                ).first()
                if exists:
                    return False
                conn.execute(insert(events).values(**row))
                conn.execute(insert(event_outbox).values(
                    event_id=event.event_id,
                    payload=event.model_dump(mode="json"),
                    published_at=None,
                ))
            return True
        except IntegrityError:
            # concurrent insert of the same event_id lost the race — the other
            # writer stored it; treat as a duplicate.
            return False

    # --- reconstruction --------------------------------------------------- #
    @staticmethod
    def _to_event(m: Any) -> Event:
        return Event(
            event_id=m["event_id"],
            customer_id=m["customer_id"],
            session_id=m["session_id"],
            signal_type=m["signal_type"],
            occurred_at=_to_utc(m["occurred_at"]),
            source=m["source"],
            context=EventContext(**(m["context"] or {})),
            consent=Consent(**m["consent"]) if m["consent"] else Consent(),
            schema_version=m["schema_version"],
        )

    def get_event(self, event_id: str) -> Event | None:
        with self.engine.connect() as conn:
            m = conn.execute(select(events).where(events.c.event_id == event_id)).mappings().first()
        return self._to_event(m) if m else None

    def count_events(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(events)).scalar_one()

    # --- read models (POA/03 §3.4) ---------------------------------------- #
    def recent_events(
        self, customer_id: str, since: datetime | None = None, limit: int = 100
    ) -> list[Event]:
        q = select(events).where(events.c.customer_id == customer_id)
        if since is not None:
            q = q.where(events.c.occurred_at >= _to_utc(since))
        q = q.order_by(events.c.occurred_at.desc()).limit(limit)
        with self.engine.connect() as conn:
            return [self._to_event(m) for m in conn.execute(q).mappings()]

    def session_events(self, session_id: str) -> list[Event]:
        q = select(events).where(events.c.session_id == session_id).order_by(events.c.occurred_at)
        with self.engine.connect() as conn:
            return [self._to_event(m) for m in conn.execute(q).mappings()]

    def last_event_at(self, customer_id: str) -> datetime | None:
        q = select(events.c.occurred_at).where(
            events.c.customer_id == customer_id
        ).order_by(events.c.occurred_at.desc()).limit(1)
        with self.engine.connect() as conn:
            row = conn.execute(q).first()
        return _to_utc(row[0]) if row else None

    def has_repeated_search(
        self, customer_id: str, pickup: str, dropoff: str, pickup_at: datetime, min_count: int = 2
    ) -> bool:
        """Signal I: the same route+dates searched >= min_count times, in
        different sessions, with no booking. Context is filtered in Python so the
        query is portable across SQLite/Postgres (prod can push this into JSONB).
        """
        search_signals = {"search_no_convert", "repeated_search"}
        target_day = _to_utc(pickup_at).date()
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(events.c.session_id, events.c.context)
                .where(events.c.customer_id == customer_id)
                .where(events.c.signal_type.in_(search_signals))
            ).all()
        sessions: set[str] = set()
        for sid, ctx in rows:
            if not ctx or ctx.get("pickup") != pickup or ctx.get("dropoff") != dropoff:
                continue
            raw = ctx.get("pickup_at")
            if raw and _to_utc(datetime.fromisoformat(raw)).date() == target_day:
                sessions.add(sid)
        return len(sessions) >= min_count

    # --- outbox access for the relay -------------------------------------- #
    def unpublished_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        q = (
            select(event_outbox.c.id, event_outbox.c.event_id, event_outbox.c.payload)
            .where(event_outbox.c.published_at.is_(None))
            .order_by(event_outbox.c.id)
            .limit(limit)
        )
        with self.engine.connect() as conn:
            return [dict(m) for m in conn.execute(q).mappings()]

    def mark_published(self, outbox_ids: list[int]) -> None:
        if not outbox_ids:
            return
        with self.engine.begin() as conn:
            conn.execute(
                update(event_outbox)
                .where(event_outbox.c.id.in_(outbox_ids))
                .values(published_at=datetime.now(UTC))
            )

    def pending_outbox_count(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(
                select(func.count()).select_from(event_outbox)
                .where(event_outbox.c.published_at.is_(None))
            ).scalar_one()

    # --- health (readiness check) ----------------------------------------- #
    def health(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
