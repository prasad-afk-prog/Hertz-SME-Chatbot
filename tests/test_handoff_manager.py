"""A8 (POA/07) — Human Handoff Manager: routing (incl. fallback/default), context
packaging, dispatch with retry, no-agent/after-hours fallback, dead-letter (never
drop), and lifecycle tracking.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from generator.fixtures import default_routing_rules
from generator.models import HandoffReason, HandoffRequest
from mocks.support_queue import SupportQueueMock
from services.event_pipeline.handoff import (
    HandoffLedger,
    HandoffManager,
    HandoffStatus,
    InMemoryDeadLetterSink,
    MockQueueAdapter,
    context_of,
    package,
    route_for,
)
from services.event_pipeline.handoff import bootstrap as handoff_bootstrap

RULES = default_routing_rules()


def req(cid="cust-1", conv="conv-1", reason=HandoffReason.cannot_resolve,
        language="en", customer_type=None, priority=None, **kw) -> HandoffRequest:
    return HandoffRequest(
        conversation_id=conv, customer_id=cid, reason=reason, language=language,
        customer_type=customer_type, priority=priority, **kw,
    )


def _ledger() -> HandoffLedger:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    handoff_bootstrap.create_all(engine)
    return HandoffLedger(engine)


# --- routing ------------------------------------------------------------ #
def test_routing_first_match_wins():
    assert route_for(context_of("de", "corporate", None), RULES).rule_id == "de_corporate_v1"
    assert route_for(context_of("en", None, None), RULES).rule_id == "en_default_v1"
    assert route_for(context_of("fr", None, None), RULES).rule_id == "catch_all_v1"   # default


def test_context_omits_unset_dimensions():
    assert context_of("en", None, None) == {"language": "en"}
    assert context_of("de", "corporate", "high") == {
        "language": "de", "customer_type": "corporate", "priority": "high"
    }


# --- dispatch: routed --------------------------------------------------- #
def test_routed_to_matched_queue_with_context():
    queue = SupportQueueMock()
    mgr = HandoffManager(MockQueueAdapter(queue), RULES, ledger=_ledger())
    res = mgr.handle(req(language="de", customer_type="corporate", booking_reference="HZAB12"))
    assert res.status == HandoffStatus.routed
    assert res.queue == "de-corporate" and res.ticket_ref
    payload = queue.tickets[0]["payload"]
    assert payload["skill"] == "billing" and payload["priority"] == "high"
    assert payload["context"]["booking_reference"] == "HZAB12"


def test_ledger_records_the_handoff():
    ledger = _ledger()
    mgr = HandoffManager(MockQueueAdapter(SupportQueueMock()), RULES, ledger=ledger)
    mgr.handle(req(language="en"))
    assert ledger.count("routed") == 1


# --- no-agent / after-hours -> fallback -------------------------------- #
def test_no_agent_goes_straight_to_fallback():
    queue = SupportQueueMock()
    mgr = HandoffManager(
        MockQueueAdapter(queue), RULES,
        agent_available=lambda q: q != "de-corporate",   # primary has no agent
    )
    res = mgr.handle(req(language="de", customer_type="corporate"))
    assert res.status == HandoffStatus.fallback
    assert res.queue == "general-de"                      # de_corporate_v1 fallback


# --- primary fails -> fallback ----------------------------------------- #
def test_primary_failure_falls_back():
    class QueueFailAdapter:
        def __init__(self, fail_queues):
            self.fail = set(fail_queues)
            self.sent: list[dict] = []

        def enqueue(self, payload):
            if payload["queue"] in self.fail:
                raise RuntimeError("no capacity")
            self.sent.append(payload)
            return f"t-{len(self.sent)}"

    mgr = HandoffManager(QueueFailAdapter({"de-corporate"}), RULES, max_attempts=2)
    res = mgr.handle(req(language="de", customer_type="corporate"))
    assert res.status == HandoffStatus.fallback and res.queue == "general-de"


# --- retry succeeds within attempts ------------------------------------ #
def test_dispatch_retries_before_giving_up():
    class FlakyAdapter:
        def __init__(self, fail_times):
            self.fail_times = fail_times
            self.sent: list[dict] = []

        def enqueue(self, payload):
            if self.fail_times > 0:
                self.fail_times -= 1
                raise RuntimeError("transient")
            self.sent.append(payload)
            return "t-ok"

    mgr = HandoffManager(FlakyAdapter(fail_times=2), RULES, max_attempts=3)
    res = mgr.handle(req(language="en"))
    assert res.status == HandoffStatus.routed and res.ticket_ref == "t-ok"


# --- all queues fail -> dead-letter (never drop) ----------------------- #
def test_total_failure_dead_letters_and_never_drops():
    dead = InMemoryDeadLetterSink()
    ledger = _ledger()
    mgr = HandoffManager(
        MockQueueAdapter(SupportQueueMock(fail=True)), RULES,
        ledger=ledger, dead_letter=dead,
    )
    res = mgr.handle(req(language="en"))
    assert res.status == HandoffStatus.dead_lettered and res.ticket_ref is None
    assert len(dead.dead) == 1                             # captured, not lost
    assert ledger.count("dead_lettered") == 1


# --- lifecycle ---------------------------------------------------------- #
def test_lifecycle_status_advances_by_ticket_ref():
    ledger = _ledger()
    mgr = HandoffManager(MockQueueAdapter(SupportQueueMock()), RULES, ledger=ledger)
    res = mgr.handle(req(language="en"))
    assert ledger.update_status(res.ticket_ref, "accepted") is True
    assert ledger.update_status(res.ticket_ref, "resolved") is True
    assert ledger.update_status("no-such-ticket", "resolved") is False   # surfaced, not silent


# --- packaging ---------------------------------------------------------- #
def test_package_summarises_without_inlining_pii():
    p = package(
        req(reason=HandoffReason.verification_failed, unresolved_claim="£42.00/day",
            booking_reference="HZAB12"),
        queue="general", skill=None, priority="normal", sla=None,
    )
    assert "verification failed" in p["summary"]
    assert "£42.00/day" in p["summary"]
    assert p["context"]["booking_reference"] == "HZAB12"
