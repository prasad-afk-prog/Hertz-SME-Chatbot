"""M11 Chatbot UI Integration & Delivery — POA/11 acceptance criteria (§6).

The four things §6 asks for:
  1. a finalised message appears proactively in the customer's session;
  2. deep-link actions render and reference the right step;
  3. replies are correlated to the right conversation and reach M12;
  4. delivery failures are detected and handled, and receipts feed M14.

Plus the two correctness properties §8 names as risks: **reply correlation**
(multiple concurrent conversations must not cross) and **idempotent inbound**
(a webhook that delivers twice must not double-post to M12).
"""
from __future__ import annotations

import pytest

from mocks.hs103 import HS103Mock
from services.conversation.delivery import (
    ActionKind,
    CorrelationStore,
    DeepLinkAction,
    DeliveryService,
    DeliveryStatus,
    HS103Adapter,
    MockHS103Adapter,
)


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def service(*, replies=None, fail=False, present=True, supports_actions=True, clock=None):
    adapter = MockHS103Adapter(
        HS103Mock(replies=replies, fail_delivery=fail),
        present=present, supports_actions=supports_actions,
    )
    return DeliveryService(adapter, clock=clock or FakeClock()), adapter


RESUME = DeepLinkAction(
    kind=ActionKind.resume_booking,
    label="Resume your booking",
    target="booking:HFB-000123#payment",
    params={"step": "payment"},
)


# --- §6.1: proactive delivery ------------------------------------------------ #
def test_message_is_delivered_proactively():
    svc, adapter = service()
    receipt = svc.deliver("conv-1", "Shall I help you finish your booking?")
    assert receipt.status is DeliveryStatus.delivered
    assert receipt.reached_customer
    assert adapter._mock.delivered == ["Shall I help you finish your booking?"]


def test_adapter_satisfies_the_protocol():
    _, adapter = service()
    assert isinstance(adapter, HS103Adapter)


def test_empty_message_is_never_delivered():
    """M09/M10 refused to produce anything safe. An empty bubble is worse than
    silence."""
    svc, adapter = service()
    receipt = svc.deliver("conv-1", "")
    assert receipt.status is DeliveryStatus.failed
    assert adapter._mock.delivered == []


# --- §3.2: presence & anti-nag ----------------------------------------------- #
def test_closed_widget_queues_rather_than_injecting():
    svc, adapter = service(present=False)
    receipt = svc.deliver("conv-1", "hello")
    assert receipt.status is DeliveryStatus.queued
    assert adapter._mock.delivered == [], "nothing should be injected into a closed widget"


def test_second_proactive_message_is_suppressed_until_acknowledged():
    svc, _ = service()
    assert svc.deliver("conv-1", "first").status is DeliveryStatus.delivered
    assert svc.deliver("conv-1", "second").status is DeliveryStatus.suppressed


def test_a_customer_reply_clears_the_anti_nag_hold():
    svc, _ = service()
    first = svc.deliver("conv-1", "first")
    svc.on_customer_message(first.thread_id, "yes please", message_id="m1")
    assert svc.deliver("conv-1", "second").status is DeliveryStatus.delivered


def test_anti_nag_does_not_apply_to_replies_in_an_active_conversation():
    """Anti-nag is a proactive-injection rule. A reply inside a live exchange is
    not a nag, and blocking it would break the M12 loop."""
    svc, _ = service()
    svc.deliver("conv-1", "first")
    assert svc.deliver("conv-1", "answering you", proactive=False).status is DeliveryStatus.delivered


def test_anti_nag_is_per_conversation():
    svc, _ = service()
    svc.deliver("conv-1", "first")
    assert svc.deliver("conv-2", "first").status is DeliveryStatus.delivered


# --- §6.2 / §3.3: deep-link actions ------------------------------------------ #
def test_deep_link_actions_are_delivered_as_structured_payloads():
    svc, adapter = service()
    svc.deliver("conv-1", "Want to pick up where you left off?", [RESUME])
    assert adapter.delivered_actions == [[{
        "kind": "resume_booking",
        "label": "Resume your booking",
        "target": "booking:HFB-000123#payment",
        "params": {"step": "payment"},
    }]]


def test_action_payload_references_the_exact_step():
    payload = RESUME.to_payload()
    assert payload["target"].endswith("#payment")
    assert payload["params"]["step"] == "payment"


def test_delivery_still_succeeds_when_the_ui_cannot_render_actions():
    """§8: HS-103's capabilities are unknown. Losing the button must not lose
    the message."""
    svc, adapter = service(supports_actions=False)
    receipt = svc.deliver("conv-1", "text still matters", [RESUME])
    assert receipt.status is DeliveryStatus.delivered
    assert adapter.delivered_actions == []
    assert adapter._mock.delivered == ["text still matters"]


# --- §6.3 / §3.4: correlation ------------------------------------------------ #
def test_reply_is_correlated_back_to_its_conversation():
    svc, _ = service()
    receipt = svc.deliver("conv-1", "hello")
    inbound = svc.on_customer_message(receipt.thread_id, "yes please", message_id="m1")
    assert inbound is not None
    assert inbound.conversation_id == "conv-1"
    assert inbound.text == "yes please"


def test_concurrent_conversations_do_not_cross():
    """§8 names reply-correlation errors as a risk. This is that test."""
    svc, _ = service()
    receipts = {cid: svc.deliver(cid, f"hello {cid}") for cid in ("c1", "c2", "c3")}
    for cid, receipt in receipts.items():
        inbound = svc.on_customer_message(receipt.thread_id, f"reply {cid}", message_id=f"m-{cid}")
        assert inbound.conversation_id == cid
        assert inbound.text == f"reply {cid}"


def test_inbound_on_an_unknown_thread_is_ignored_not_raised():
    """A 500 back to HS-103 would just make it retry the same unusable event."""
    svc, _ = service()
    assert svc.on_customer_message("thread-we-never-saw", "hi", message_id="m1") is None


def test_duplicate_webhook_delivery_is_idempotent():
    svc, _ = service()
    receipt = svc.deliver("conv-1", "hello")
    first = svc.on_customer_message(receipt.thread_id, "yes", message_id="dup-1")
    second = svc.on_customer_message(receipt.thread_id, "yes", message_id="dup-1")
    assert first is not None
    assert second is None, "a repeated webhook must not post a duplicate turn to M12"


def test_correlation_store_maps_both_directions():
    store = CorrelationStore()
    store.bind("conv-1", "thread-9")
    assert store.thread_for("conv-1") == "thread-9"
    assert store.conversation_for("thread-9") == "conv-1"


def test_rebinding_a_conversation_to_a_new_thread_is_refused():
    """Silently rebinding would misroute every subsequent reply."""
    store = CorrelationStore()
    store.bind("conv-1", "thread-9")
    store.bind("conv-1", "thread-9")            # idempotent rebind is fine
    with pytest.raises(ValueError, match="already bound"):
        store.bind("conv-1", "thread-10")


# --- §6.4: failures and receipts --------------------------------------------- #
def test_delivery_failure_is_retried_then_reported():
    svc, _ = service(fail=True)
    receipt = svc.deliver("conv-1", "hello")
    assert receipt.status is DeliveryStatus.failed
    assert receipt.attempts == 3
    assert "HS-103 delivery failed" in (receipt.reason or "")


def test_a_failed_delivery_does_not_bind_a_correlation():
    """Binding on failure would route a later reply to a message that never
    arrived."""
    svc, _ = service(fail=True)
    svc.deliver("conv-1", "hello")
    assert len(svc.correlation) == 0


def test_read_receipts_feed_the_engagement_metric():
    clock = FakeClock()
    svc, _ = service(clock=clock)
    svc.deliver("conv-1", "hello")
    clock.advance(12.0)
    assert svc.mark_read("conv-1")
    assert svc.receipts[-1].read_at == 12.0
    assert svc.engagement["read"] == 1


def test_marking_an_undelivered_conversation_read_is_a_no_op():
    svc, _ = service(fail=True)
    svc.deliver("conv-1", "hello")
    assert not svc.mark_read("conv-1")


def test_engagement_counts_every_outcome_for_m14():
    svc, _ = service()
    svc.deliver("c1", "hello")                       # delivered
    svc.deliver("c1", "again")                       # suppressed
    svc.deliver("c2", "")                            # failed (empty)
    svc.mark_read("c1")

    assert svc.engagement == {
        "delivered": 1, "queued": 0, "suppressed": 1, "failed": 1, "read": 1,
    }


def test_every_delivery_attempt_produces_a_receipt():
    """M14 needs the whole picture, including the messages that never went out."""
    svc, _ = service()
    svc.deliver("c1", "hello")
    svc.deliver("c1", "again")
    svc.deliver("c2", "")
    assert len(svc.receipts) == 3
    assert all(r.conversation_id for r in svc.receipts)
