"""M12 Customer Response & Multi-turn Manager — POA/12 acceptance criteria (§6).

The four §6 criteria:
  1. no response within the window ⇒ closed as no-engagement, counted for M14;
  2. a responding customer gets coherent multi-turn replies with retained
     context, each verified where needed;
  3. on resolution the customer gets a working deep link, and a subsequent
     booking is attributed to the intervention;
  4. when the bot can't help, a handoff event with full context reaches M07 —
     never handled ad-hoc inline.

Two behaviours get their own tests because a naive implementation gets them
backwards:

* **hitting max-turns must ESCALATE, not close.** A customer dropped
  mid-conversation is worse than one handed to a person;
* **a booking before the conversation is not attributable** — claiming it would
  inflate the conversion metric the whole feature is judged on.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from generator.models import Intent, LLMResponse, MessageKind, TerminalState
from mocks.booking_api import BookingAPIMock
from mocks.hs103 import HS103Mock
from mocks.llm_provider import LLMProviderMock
from services.conversation.claim_verification import ClaimVerificationService, MockClientAdapter
from services.conversation.delivery import DeliveryService, MockHS103Adapter
from services.conversation.llm import LLMConfig, LLMService, MockProviderAdapter
from services.conversation.orchestrator.state import (
    Conversation,
    ConversationStatus,
    InMemoryConversationStore,
    Turn,
    TurnRole,
)
from services.conversation.response import (
    AttributionWindow,
    BookingSignal,
    HandoffReason,
    InMemoryHandoffSink,
    ResolutionDetector,
    ResponseManager,
    TurnResult,
    Verdict,
)

_TZ = timezone.utc
T0 = datetime(2026, 9, 1, 10, 0, tzinfo=_TZ)


def good(text="Happy to help you finish that booking — shall I hold the vehicle?"):
    return LLMResponse(text=text, confidence=0.9)


def build(world, *, llm_response=None, llm_timeout=False, fail_delivery=False, max_turns=4):
    store = InMemoryConversationStore()
    llm = LLMService(
        MockProviderAdapter(LLMProviderMock(llm_response or good(), timeout=llm_timeout)),
        LLMConfig(),
    )
    verifier = ClaimVerificationService(MockClientAdapter(BookingAPIMock(world)))
    delivery = DeliveryService(MockHS103Adapter(HS103Mock(fail_delivery=fail_delivery)))
    sink = InMemoryHandoffSink()
    mgr = ResponseManager(
        store, llm, verifier, delivery,
        handoff_sink=sink, max_turns=max_turns,
        no_response_after=timedelta(hours=2), now=lambda: T0,
    )
    return mgr, store, sink


def seeded(store, *, status=ConversationStatus.open, delivered_at=T0) -> Conversation:
    conversation = Conversation(
        conversation_id="conv-1", customer_id="cust-1", trigger_id="booking_abandoned_v1",
        locale="en", status=status, created_at=delivered_at.timestamp(),
    )
    conversation.add_turn(Turn(
        role=TurnRole.bot, text="Your booking is still saved — shall I help?",
        at=delivered_at.timestamp(), message_kind="llm",
    ))
    store.create(conversation)
    return conversation


# =========================================================================== #
# §6.1 — AE / AF: no response
# =========================================================================== #
def test_no_reply_inside_the_window_is_not_yet_no_engagement(world):
    mgr, store, _ = build(world)
    seeded(store)
    assert mgr.check_no_response("conv-1", T0 + timedelta(minutes=30)) is None
    assert store.get("conv-1").status is ConversationStatus.open


def test_no_reply_past_the_window_closes_as_no_engagement(world):
    """Injected time — the window is asserted, never slept for."""
    mgr, store, _ = build(world)
    seeded(store)
    outcome = mgr.check_no_response("conv-1", T0 + timedelta(hours=3))

    assert outcome is not None
    assert outcome.terminal is TerminalState.no_engagement
    assert outcome.status is ConversationStatus.no_engagement
    assert store.get("conv-1").status is ConversationStatus.no_engagement


def test_the_no_engagement_outcome_is_recorded_for_m14(world):
    mgr, store, _ = build(world)
    seeded(store)
    mgr.check_no_response("conv-1", T0 + timedelta(hours=3))

    assert len(mgr.outcomes) == 1
    assert mgr.outcomes[0].terminal is TerminalState.no_engagement


def test_the_timeout_check_is_idempotent(world):
    """Celery will retry; a second firing must not double-count."""
    mgr, store, _ = build(world)
    seeded(store)
    assert mgr.check_no_response("conv-1", T0 + timedelta(hours=3)) is not None
    assert mgr.check_no_response("conv-1", T0 + timedelta(hours=4)) is None
    assert len(mgr.outcomes) == 1


def test_a_conversation_that_never_delivered_is_not_counted(world):
    mgr, store, _ = build(world)
    seeded(store, status=ConversationStatus.failed)
    assert mgr.check_no_response("conv-1", T0 + timedelta(hours=3)) is None


# =========================================================================== #
# §6.2 — AG: multi-turn with retained context
# =========================================================================== #
def test_a_reply_starts_the_multi_turn_loop(world):
    mgr, store, _ = build(world)
    seeded(store)
    result = mgr.on_customer_reply("conv-1", "what vehicles do you have?", at=T0)

    assert isinstance(result, TurnResult)
    assert result.verdict is Verdict.unresolved
    assert result.bot_text
    assert store.get("conv-1").status is ConversationStatus.active


def test_context_is_retained_across_turns(world):
    """The API is stateless, so 'retained context' means resending the
    transcript — and the transcript must actually grow."""
    mgr, store, _ = build(world)
    seeded(store)
    mgr.on_customer_reply("conv-1", "what vehicles do you have?", at=T0)
    mgr.on_customer_reply("conv-1", "and how much for three days?", at=T0)

    turns = store.get("conv-1").turns
    texts = [t.text for t in turns]
    assert "what vehicles do you have?" in texts
    assert "and how much for three days?" in texts
    assert sum(1 for t in turns if t.role is TurnRole.customer) == 2


def test_every_bot_turn_is_persisted_with_its_message_kind(world):
    mgr, store, _ = build(world)
    seeded(store)
    mgr.on_customer_reply("conv-1", "tell me more", at=T0)

    bot_turns = [t for t in store.get("conv-1").turns if t.role is TurnRole.bot]
    assert len(bot_turns) == 2                      # the opener plus this reply
    assert bot_turns[-1].message_kind is not None


def test_a_late_reply_to_a_finished_conversation_is_ignored(world):
    mgr, store, _ = build(world)
    seeded(store)
    mgr.check_no_response("conv-1", T0 + timedelta(hours=3))
    assert mgr.on_customer_reply("conv-1", "sorry, just seen this", at=T0) is None


def test_a_reply_on_an_unknown_conversation_is_ignored(world):
    mgr, _, _ = build(world)
    assert mgr.on_customer_reply("no-such-conversation", "hello", at=T0) is None


def test_provider_outage_mid_conversation_still_replies(world):
    """M09's fallback covers the turn; the loop must not stall."""
    mgr, store, _ = build(world, llm_timeout=True)
    seeded(store)
    result = mgr.on_customer_reply("conv-1", "are you still there?", at=T0)

    assert isinstance(result, TurnResult)
    assert result.used_fallback
    assert result.bot_text


# =========================================================================== #
# §6.4 / AK — handoff
# =========================================================================== #
def test_an_explicit_request_for_a_person_hands_off_immediately(world):
    mgr, store, sink = build(world)
    seeded(store)
    outcome = mgr.on_customer_reply("conv-1", "just put me through to an agent", at=T0)

    assert outcome.terminal is TerminalState.handed_off
    assert len(sink.events) == 1
    assert sink.events[0].reason is HandoffReason.customer_requested
    assert store.get("conv-1").status is ConversationStatus.handed_off


def test_the_handoff_event_carries_the_full_transcript(world):
    """§6: context preserved end to end. A handoff that makes the customer
    repeat themselves is the failure this feature exists to avoid."""
    mgr, store, sink = build(world)
    seeded(store)
    mgr.on_customer_reply("conv-1", "what vehicles do you have?", at=T0)
    mgr.on_customer_reply("conv-1", "this isn't helping", at=T0)

    event = sink.events[0]
    texts = [t.text for t in event.transcript]
    assert "what vehicles do you have?" in texts
    assert "this isn't helping" in texts
    assert event.turn_count >= 3
    assert event.customer_id == "cust-1"
    assert event.trigger_id == "booking_abandoned_v1"


@pytest.mark.parametrize(
    "intent, reason",
    [(Intent.complaint, HandoffReason.complaint),
     (Intent.claim_dispute, HandoffReason.claim_dispute)],
)
def test_intents_a_bot_must_never_resolve_go_straight_to_a_person(world, intent, reason):
    """The S3 trees (CV-12, CV-13) pin this expectation; here it becomes
    behaviour."""
    mgr, store, sink = build(world)
    seeded(store)
    outcome = mgr.on_customer_reply("conv-1", "the van never turned up", intent=intent, at=T0)

    assert outcome.terminal is TerminalState.handed_off
    assert sink.events[0].reason is reason


def test_hitting_max_turns_escalates_rather_than_closing(world):
    """The behaviour a naive implementation gets backwards. A customer dropped
    mid-conversation is worse than one handed to a person."""
    mgr, store, sink = build(world, max_turns=2)
    seeded(store)

    first = mgr.on_customer_reply("conv-1", "hmm", at=T0)
    assert isinstance(first, TurnResult)

    second = mgr.on_customer_reply("conv-1", "still not sure", at=T0)
    assert second.terminal is TerminalState.handed_off, "max turns must escalate, not close"
    assert sink.events[0].reason is HandoffReason.max_turns
    assert store.get("conv-1").status is not ConversationStatus.closed


def test_delivery_failure_mid_conversation_hands_off(world):
    """We cannot continue a conversation we cannot speak into."""
    mgr, store, sink = build(world, fail_delivery=True)
    seeded(store)
    outcome = mgr.on_customer_reply("conv-1", "tell me more", at=T0)

    assert outcome.terminal is TerminalState.handed_off
    assert any("delivery failed" in n for n in sink.events[0].notes)


def test_the_bot_never_routes_the_handoff_itself(world):
    """§2: raised, never handled inline. M12 emits and stops — routing is M04's
    job, then M07's."""
    mgr, store, sink = build(world)
    seeded(store)
    mgr.on_customer_reply("conv-1", "talk to a human please", at=T0)

    event = sink.events[0]
    assert not hasattr(event, "queue")
    assert not hasattr(event, "agent_id")
    assert not hasattr(event, "skill")


# =========================================================================== #
# §6.3 — AI / AJ: resolution, deep link, attribution
# =========================================================================== #
def test_explicit_confirmation_resolves_and_surfaces_a_deep_link(world):
    mgr, store, _ = build(world)
    seeded(store)
    outcome = mgr.on_customer_reply("conv-1", "yes please, book it", at=T0)

    assert outcome.deep_link is not None
    assert outcome.deep_link.target.endswith("#resume")
    assert store.get("conv-1").status is ConversationStatus.deep_link


def test_resolution_alone_is_not_conversion(world):
    """A deep link surfaced is not a booking made.

    This caught a real bug: `reference.terminal_state(responded=True,
    resolves=True)` returns `converted`, so calling it at resolution counted a
    booking that had not happened — inflating the exact metric M14 reports.
    `terminal` stays None until AJ attributes a real booking. See POA/12 §11.
    """
    mgr, store, _ = build(world)
    seeded(store)
    outcome = mgr.on_customer_reply("conv-1", "perfect, thanks", at=T0)

    assert outcome.terminal is None, "resolved is not a terminal state"
    assert not outcome.converted
    assert outcome.booking_id is None


def test_the_shared_terminal_state_spec_cannot_express_resolved_not_booked(world):
    """The finding, pinned. `TerminalState` has three members but this flow has
    four outcomes; the fourth has no name yet, so M12 represents it as None
    rather than forking the spec."""
    from generator.reference import terminal_state

    assert terminal_state(responded=True, resolves=True) is TerminalState.converted
    assert len(list(TerminalState)) == 3


def test_a_booking_inside_the_window_is_attributed(world):
    mgr, store, _ = build(world)
    seeded(store)
    mgr.on_customer_reply("conv-1", "yes please", at=T0)

    attributed = mgr.attribute_booking(
        BookingSignal("cust-1", "bk-1", T0 + timedelta(hours=2))
    )
    assert attributed is not None
    assert attributed.converted
    assert attributed.booking_id == "bk-1"
    assert store.get("conv-1").status is ConversationStatus.converted


def test_the_attribution_boundary_is_inclusive_and_tested_at_both_edges(world):
    window = AttributionWindow(window=timedelta(hours=24))
    assert window.attributes(T0, T0 + timedelta(hours=24)) is True, "the edge counts"
    assert window.attributes(T0, T0 + timedelta(hours=24, seconds=1)) is False


def test_a_booking_before_the_conversation_is_never_attributed(world):
    """The customer was already going to book. Claiming it would inflate the
    conversion metric the whole feature is judged on."""
    window = AttributionWindow()
    assert window.attributes(T0, T0 - timedelta(minutes=1)) is False

    mgr, store, _ = build(world)
    seeded(store)
    mgr.on_customer_reply("conv-1", "yes please", at=T0)
    assert mgr.attribute_booking(BookingSignal("cust-1", "bk-early", T0 - timedelta(hours=1))) is None


def test_a_booking_outside_the_window_is_not_attributed(world):
    mgr, store, _ = build(world)
    seeded(store)
    mgr.on_customer_reply("conv-1", "yes please", at=T0)
    assert mgr.attribute_booking(BookingSignal("cust-1", "bk-late", T0 + timedelta(days=3))) is None


def test_another_customers_booking_is_never_attributed(world):
    mgr, store, _ = build(world)
    seeded(store)
    mgr.on_customer_reply("conv-1", "yes please", at=T0)
    assert mgr.attribute_booking(BookingSignal("someone-else", "bk-x", T0 + timedelta(hours=1))) is None


def test_a_booking_is_attributed_only_once(world):
    mgr, store, _ = build(world)
    seeded(store)
    mgr.on_customer_reply("conv-1", "yes please", at=T0)

    assert mgr.attribute_booking(BookingSignal("cust-1", "bk-1", T0 + timedelta(hours=1))) is not None
    assert mgr.attribute_booking(BookingSignal("cust-1", "bk-2", T0 + timedelta(hours=2))) is None


# =========================================================================== #
# The resolution detector on its own
# =========================================================================== #
def test_resolved_has_to_be_earned():
    """Default is unresolved — erring toward a person costs an agent's time;
    erring the other way strands a customer who still needs help."""
    detector = ResolutionDetector()
    assert detector.assess("hmm, maybe").verdict is Verdict.unresolved
    assert detector.assess("ok").verdict is Verdict.unresolved
    assert detector.assess("yes please").verdict is Verdict.resolved


def test_frustration_is_detected_as_stuck():
    detector = ResolutionDetector()
    for phrase in ("this isn't helping", "you're not listening", "i already told you"):
        assert detector.assess(phrase).verdict is Verdict.stuck, phrase


def test_an_unverifiable_claim_escalates_rather_than_papering_over_it():
    """We could not state a fact safely, so we do not pretend we helped."""
    outcome = ResolutionDetector().assess("so how much is it?", claim_unverifiable=True)
    assert outcome.verdict is Verdict.stuck
    assert outcome.reason is HandoffReason.verification_failed


def test_the_detector_records_what_tipped_it():
    """A resolution decision without its evidence is unauditable."""
    assert ResolutionDetector().assess("yes please").evidence
    assert ResolutionDetector().assess("talk to a human").evidence
