"""A2 (POA/03) — Event Store: idempotent transactional write+outbox, the
at-least-once relay to the stream, and the read models for signals I/J.

Runs against in-memory SQLite (StaticPool so the schema persists across the
store's connections); the same code runs on Postgres in prod.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from generator.models import Event, EventContext, SignalType
from services.event_pipeline.store import (
    InMemoryStreamPublisher,
    OutboxRelay,
    SqlEventStore,
    bootstrap,
)

TZ = timezone.utc
T0 = datetime(2026, 9, 1, 10, 0, tzinfo=TZ)


def make_event(eid, cid="hfb-cust-1", sid="sess-1", signal=SignalType.search_no_convert,
               when=None, **ctx) -> Event:
    return Event(
        event_id=eid, customer_id=cid, session_id=sid, signal_type=signal,
        occurred_at=when or T0, context=EventContext(**ctx),
    )


@pytest.fixture
def store() -> SqlEventStore:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    bootstrap.create_all(engine)
    return SqlEventStore(engine)


# --- write + outbox ----------------------------------------------------- #
def test_write_persists_and_enqueues_outbox(store):
    assert store.write_event(make_event("e1")) is True
    assert store.count_events() == 1
    assert store.pending_outbox_count() == 1
    got = store.get_event("e1")
    assert got is not None and got.customer_id == "hfb-cust-1"


def test_write_is_idempotent(store):
    e = make_event("e1")
    assert store.write_event(e) is True
    assert store.write_event(e) is False          # same event_id -> no-op
    assert store.count_events() == 1
    assert store.pending_outbox_count() == 1       # not double-enqueued


# --- relay -------------------------------------------------------------- #
def test_relay_publishes_then_marks_and_is_idempotent(store):
    for i in range(3):
        store.write_event(make_event(f"e{i}"))
    pub = InMemoryStreamPublisher()
    relay = OutboxRelay(store, pub)

    assert relay.run_once() == 3
    assert sorted(pub.event_ids) == ["e0", "e1", "e2"]
    assert store.pending_outbox_count() == 0
    assert relay.run_once() == 0                    # nothing left; no re-publish
    assert len(pub.messages) == 3


def test_relay_is_at_least_once_on_publish_failure(store):
    store.write_event(make_event("e0"))
    store.write_event(make_event("e1"))
    pub = InMemoryStreamPublisher(fail_times=1)     # first publish call throws
    relay = OutboxRelay(store, pub)

    with pytest.raises(RuntimeError):
        relay.run_once()
    assert store.pending_outbox_count() == 2         # nothing marked on failure
    assert pub.messages == []

    assert relay.run_once() == 2                      # recovers, no loss
    assert sorted(pub.event_ids) == ["e0", "e1"]
    assert store.pending_outbox_count() == 0


def test_no_event_lost_under_flaky_publisher(store):
    """POA/03 §7 property: every stored event reaches the stream exactly once
    even across transient publish failures."""
    ids = [f"e{i}" for i in range(5)]
    for eid in ids:
        store.write_event(make_event(eid))
    pub = InMemoryStreamPublisher(fail_times=2)
    relay = OutboxRelay(store, pub)

    for _ in range(20):
        if store.pending_outbox_count() == 0:
            break
        try:
            relay.run_once()
        except RuntimeError:
            pass
    assert store.pending_outbox_count() == 0
    assert sorted(pub.event_ids) == sorted(ids)      # each event once, none lost


# --- read models -------------------------------------------------------- #
def test_recent_events_desc_and_since(store):
    store.write_event(make_event("old", when=T0 - timedelta(days=2)))
    store.write_event(make_event("mid", when=T0 - timedelta(hours=2)))
    store.write_event(make_event("new", when=T0))
    recent = store.recent_events("hfb-cust-1")
    assert [e.event_id for e in recent] == ["new", "mid", "old"]     # newest first
    since = store.recent_events("hfb-cust-1", since=T0 - timedelta(hours=3))
    assert {e.event_id for e in since} == {"new", "mid"}


def test_session_events_filter_and_order(store):
    store.write_event(make_event("a", sid="sess-X", when=T0))
    store.write_event(make_event("b", sid="sess-X", when=T0 + timedelta(minutes=1)))
    store.write_event(make_event("c", sid="sess-Y", when=T0))
    ev = store.session_events("sess-X")
    assert [e.event_id for e in ev] == ["a", "b"]                    # oldest first, filtered


def test_repeated_search_signal_i(store):
    kw = dict(pickup="LHR", dropoff="LHR", pickup_at=T0)
    store.write_event(make_event("s1", sid="sess-a", **kw))
    store.write_event(make_event("s2", sid="sess-b", **kw))          # same route, 2nd session
    assert store.has_repeated_search("hfb-cust-1", "LHR", "LHR", T0, min_count=2) is True
    # a different route is not the same repeated search
    assert store.has_repeated_search("hfb-cust-1", "MAN", "MAN", T0, min_count=2) is False


def test_last_event_at(store):
    store.write_event(make_event("a", when=T0 - timedelta(days=1)))
    store.write_event(make_event("b", when=T0))
    assert store.last_event_at("hfb-cust-1") == T0
    assert store.last_event_at("nobody") is None


def test_event_roundtrips_with_context_and_tz(store):
    store.write_event(make_event(
        "rt", signal=SignalType.booking_abandoned, when=T0,
        pickup="LHR", dropoff="MAN", vehicle_class="ICAR", pickup_at=T0 + timedelta(days=3),
    ))
    got = store.get_event("rt")
    assert got.signal_type == SignalType.booking_abandoned
    assert got.context.pickup == "LHR" and got.context.dropoff == "MAN"
    assert got.context.vehicle_class == "ICAR"
    assert got.occurred_at == T0                      # tz-aware, preserved
    assert got.occurred_at.tzinfo is not None


def test_health_true_on_live_engine(store):
    assert store.health() is True


# --- app wiring --------------------------------------------------------- #
def test_event_pipeline_wires_postgres_readiness():
    from fastapi.testclient import TestClient

    from services.event_pipeline.main import build_app
    from services.platform import Settings

    app = build_app(Settings(database_url="sqlite://", environment="local"))
    client = TestClient(app)
    body = client.get("/readyz").json()
    assert "postgres" in body["checks"]               # A2 readiness check registered
    assert body["status"] == "ready"
    assert client.get("/").json()["status"] == "ingestion+store wired"
