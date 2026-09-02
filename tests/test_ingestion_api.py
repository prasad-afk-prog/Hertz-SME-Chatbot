"""A4 (POA/02) — Event Ingestion API: validation, idempotency, identity binding,
rate limiting, batch partial-success, and the write-through to the A2 store.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool

from services.event_pipeline.ingestion import IngestionService, InMemoryRateLimiter, Principal
from services.event_pipeline.main import build_app
from services.event_pipeline.store import SqlEventStore, bootstrap
from services.platform import Settings


def _new_store() -> SqlEventStore:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    bootstrap.create_all(engine)
    return SqlEventStore(engine)


def _event(eid: str = "e1", cid: str = "cust-1", **context) -> dict:
    body = {
        "event_id": eid, "customer_id": cid, "session_id": "sess-1",
        "signal_type": "search_no_convert", "occurred_at": "2026-09-01T10:00:00Z",
    }
    if context:
        body["context"] = context
    return body


@pytest.fixture
def store() -> SqlEventStore:
    return _new_store()


@pytest.fixture
def client(store) -> TestClient:
    return TestClient(build_app(Settings(environment="local"), event_store=store))


# --- happy path + idempotency ------------------------------------------ #
def test_ingest_single_is_accepted_and_written_through(client, store):
    r = client.post("/v1/events", json=_event())
    assert r.status_code == 202
    assert r.json() == {"event_id": "e1", "status": "accepted"}
    assert store.count_events() == 1
    assert store.pending_outbox_count() == 1        # enqueued for the relay


def test_duplicate_event_id_is_deduped(client, store):
    assert client.post("/v1/events", json=_event()).json()["status"] == "accepted"
    r2 = client.post("/v1/events", json=_event())
    assert r2.status_code == 202
    assert r2.json()["status"] == "duplicate"
    assert store.count_events() == 1                 # not stored twice


# --- validation / PII allow-list --------------------------------------- #
def test_malformed_event_is_422(client):
    assert client.post("/v1/events", json={"event_id": "x"}).status_code == 422


def test_unknown_context_field_rejected(client):
    # extra='forbid' on EventContext IS the PII field allow-list (POA/15 §4)
    r = client.post("/v1/events", json=_event(pickup="LHR", email="a@b.com"))
    assert r.status_code == 422


# --- batch partial success --------------------------------------------- #
def test_batch_partial_success(client, store):
    body = {"events": [_event("b1"), {"event_id": "bad"}, _event("b2")]}
    r = client.post("/v1/events:batch", json=body)
    assert r.status_code == 202
    j = r.json()
    assert (j["accepted"], j["failed"]) == (2, 1)
    assert store.count_events() == 2                 # the two valid ones written
    assert j["results"][1]["status"] == "invalid"


def test_batch_dedupes_within_and_across(client, store):
    r = client.post("/v1/events:batch", json={"events": [_event("d1"), _event("d1")]})
    j = r.json()
    assert (j["accepted"], j["duplicates"]) == (1, 1)
    assert store.count_events() == 1


# --- auth -------------------------------------------------------------- #
def test_api_key_required_when_configured():
    app = build_app(Settings(environment="local", ingest_api_key="s3cret"), event_store=_new_store())
    c = TestClient(app)
    assert c.post("/v1/events", json=_event()).status_code == 401
    ok = c.post("/v1/events", json=_event(), headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 202


# --- identity binding -------------------------------------------------- #
def test_identity_conflict_is_409(client):
    class BoundAuth:
        def authenticate(self, request):            # source carries a verified identity
            return Principal(source="portal", customer_id="cust-A")

    client.app.state.authenticator = BoundAuth()
    r = client.post("/v1/events", json=_event(cid="cust-B"))   # body disagrees
    assert r.status_code == 409


# --- rate limiting ----------------------------------------------------- #
def test_rate_limited_is_429(client, store):
    client.app.state.ingestion_service = IngestionService(store, InMemoryRateLimiter(limit_per_min=1))
    assert client.post("/v1/events", json=_event("r1")).status_code == 202
    assert client.post("/v1/events", json=_event("r2")).status_code == 429   # same customer


# --- store outage ------------------------------------------------------ #
def test_store_outage_is_503(client):
    class BoomStore:
        def write_event(self, event):
            raise SQLAlchemyError("db down")

    client.app.state.ingestion_service = IngestionService(BoomStore())
    assert client.post("/v1/events", json=_event()).status_code == 503


# --- end-to-end: API -> store -> outbox -> relay -> stream ------------- #
def test_ingested_events_reach_the_stream(client, store):
    from services.event_pipeline.store import InMemoryStreamPublisher, OutboxRelay

    client.post("/v1/events", json=_event("x1"))
    client.post("/v1/events", json=_event("x2"))
    pub = InMemoryStreamPublisher()
    OutboxRelay(store, pub).run_once()
    assert sorted(pub.event_ids) == ["x1", "x2"]     # both ingested events fanned out
    assert store.pending_outbox_count() == 0

