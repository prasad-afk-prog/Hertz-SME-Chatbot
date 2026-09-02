"""A3 (POA/01) — Behavioural Event Capture SDK: detector payloads, consent
gating, session correlation, offline buffering + retry (no loss), and the
SDK -> A4 Ingestion contract (incl. idempotency).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from generator.models import BookingStep, Consent, SignalType
from services.event_pipeline.capture import (
    CaptureClient,
    HttpTransport,
    InMemoryTransport,
)
from services.event_pipeline.main import build_app
from services.event_pipeline.store import SqlEventStore
from services.event_pipeline.store import bootstrap as store_bootstrap
from services.platform import Settings

T0 = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
ROUTE = dict(pickup="LHR", dropoff="MAN", pickup_at=T0, return_at=T0)


# --- capture + detectors ------------------------------------------------ #
def test_capture_correlates_session_and_is_schema_valid():
    c = CaptureClient(InMemoryTransport(), "cust-1")
    assert c.search_no_convert(**ROUTE) is True
    (event,) = c.buffer.snapshot()
    assert event.session_id == c.session.session_id
    assert event.signal_type == SignalType.search_no_convert
    assert event.context.pickup == "LHR"


def test_detectors_carry_the_right_payload():
    c = CaptureClient(InMemoryTransport(), "cust-1")
    c.booking_abandoned(step=BookingStep.payment, **ROUTE)
    c.error_hit(error_code="PAYMENT_DECLINED")
    c.extended_dwell(dwell_ms=95000)
    ba, err, dwell = c.buffer.snapshot()
    assert ba.signal_type == SignalType.booking_abandoned and ba.context.step == BookingStep.payment
    assert err.context.error_code == "PAYMENT_DECLINED"
    assert dwell.context.dwell_ms == 95000


def test_session_restart_changes_the_session_id():
    c = CaptureClient(InMemoryTransport(), "cust-1")
    first = c.session.session_id
    c.search_no_convert(**ROUTE)
    c.start_session()
    assert c.session.session_id != first
    c.search_no_convert(**ROUTE)
    ids = {e.session_id for e in c.buffer.snapshot()}
    assert ids == {first, c.session.session_id}


# --- consent ------------------------------------------------------------ #
def test_analytics_consent_off_drops_events():
    c = CaptureClient(InMemoryTransport(), "cust-1", consent=Consent(analytics=False))
    assert c.search_no_convert(**ROUTE) is False
    assert len(c.buffer) == 0
    assert c.flush().sent == 0


# --- buffering + retry -------------------------------------------------- #
def test_flush_sends_and_clears():
    t = InMemoryTransport()
    c = CaptureClient(t, "cust-1")
    c.search_no_convert(**ROUTE)
    c.session_ended_no_booking()
    res = c.flush()
    assert res.ok and res.sent == 2
    assert len(t.sent_events) == 2 and len(c.buffer) == 0


def test_no_loss_across_transient_outage():
    t = InMemoryTransport(fail_times=2)
    c = CaptureClient(t, "cust-1")
    for _ in range(3):
        c.search_no_convert(**ROUTE)

    assert c.flush().ok is False and len(c.buffer) == 3     # kept
    assert c.flush().ok is False and len(c.buffer) == 3     # still kept
    ok = c.flush()
    assert ok.ok and ok.sent == 3 and len(c.buffer) == 0
    assert len(t.sent_events) == 3                          # every event delivered, once


def test_buffer_is_capped():
    c = CaptureClient(InMemoryTransport(), "cust-1", max_buffer=2)
    for _ in range(3):
        c.search_no_convert(**ROUTE)
    assert len(c.buffer) == 2                               # oldest dropped, bounded


# --- SDK -> A4 contract (integration) ---------------------------------- #
@pytest.fixture
def a4():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    store_bootstrap.create_all(engine)
    store = SqlEventStore(engine)
    app = build_app(Settings(environment="local"), event_store=store)
    return TestClient(app), store


def _poster(client: TestClient):
    def post(url, payload, headers):
        r = client.post("/v1/events:batch", json=payload, headers=headers)
        return r.status_code, (r.json() if r.content else {})
    return post


def test_sdk_delivers_valid_events_to_a4(a4):
    client, store = a4
    transport = HttpTransport("http://testserver", post=_poster(client))
    c = CaptureClient(transport, "cust-1")
    c.booking_abandoned(step=BookingStep.payment, **ROUTE)
    c.search_no_convert(**ROUTE)

    res = c.flush()
    assert res.ok and res.sent == 2
    assert store.count_events() == 2                        # accepted + stored by A4
    stored = store.recent_events("cust-1")
    assert any(e.signal_type == SignalType.booking_abandoned and e.context.step == BookingStep.payment
               for e in stored)


def test_redelivered_batch_is_deduped_by_a4(a4):
    client, store = a4
    transport = HttpTransport("http://testserver", post=_poster(client))
    c = CaptureClient(transport, "cust-1")
    c.search_no_convert(**ROUTE)
    payload = {"events": [e.model_dump(mode="json") for e in c.buffer.snapshot()]}

    c.flush()                                               # delivered once
    assert store.count_events() == 1
    transport.send_batch(payload)                           # a lost-ack retry re-sends
    assert store.count_events() == 1                        # A4 deduped on event_id
