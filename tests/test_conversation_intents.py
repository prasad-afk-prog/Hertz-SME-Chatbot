"""S3 — scripted conversation trees (POA/16 §16.4 intents, §16.6 Phase-1 format).

What these prove, beyond "the objects validate":
  * all 17 mandated intents are covered, and stay covered;
  * every price/availability claim made in a conversation is grounded in the
    SAME world the booking-API mock reads — so M10 can verify it for real;
  * a deliberately wrong quote is wrong *against live data*, and the wrong token
    is pinned in `delivered_excludes`;
  * requirement changes mid-conversation override slots rather than accumulate;
  * the reply source is swappable, which is what §16.6 requires for Phase 2.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from generator.intents import (
    IntentScenarioComposer,
    ReplyContext,
    ReplySource,
    ScriptedReplySource,
    final_slots,
)
from generator.models import (
    ClaimKind,
    ConversationOutcome,
    ConversationScenario,
    Intent,
    Speaker,
)


@pytest.fixture(scope="module")
def conversations(world) -> list[ConversationScenario]:
    return IntentScenarioComposer(world).all()


# --- coverage --------------------------------------------------------------- #
def test_all_17_intents_covered(conversations):
    assert {c.intent for c in conversations} == set(Intent)
    assert len(set(Intent)) == 17


def test_conversation_ids_unique(conversations):
    ids = [c.conversation_id for c in conversations]
    assert len(ids) == len(set(ids))


def test_every_conversation_has_a_customer_opener_and_a_bot_reply(conversations):
    for cv in conversations:
        assert cv.turns[0].speaker is Speaker.customer, cv.conversation_id
        bot_turns = [t for t in cv.turns if t.speaker is Speaker.bot]
        assert len(bot_turns) >= cv.expected.min_bot_turns, cv.conversation_id


def test_turn_numbers_are_sequential(conversations):
    for cv in conversations:
        assert [t.turn for t in cv.turns] == list(range(1, len(cv.turns) + 1)), cv.conversation_id


def test_only_customer_turns_carry_intent_only_bot_turns_carry_claims(conversations):
    for cv in conversations:
        for t in cv.turns:
            if t.speaker is Speaker.customer:
                assert t.intent is not None, f"{cv.conversation_id} turn {t.turn}"
                assert not t.claims, f"{cv.conversation_id} turn {t.turn}"
            else:
                assert t.intent is None, f"{cv.conversation_id} turn {t.turn}"


# --- world grounding (the point of the whole dataset) ----------------------- #
def test_every_price_claim_is_grounded_in_the_world(conversations, world):
    """A quoted price must correspond to a real (location, class, date) the
    booking API can be asked about — otherwise M10 could never verify it."""
    seen = 0
    for cv in conversations:
        for t in cv.turns:
            for claim in t.claims:
                if claim.kind is not ClaimKind.price:
                    continue
                live = world.rate(claim.pickup, claim.vehicle_class, claim.pickup_at.date())
                assert isinstance(live, Decimal)
                seen += 1
    assert seen >= 5, "expected several verifiable price claims across the trees"


def test_wrong_quote_is_wrong_against_live_data_and_is_excluded(conversations, world):
    cv = next(c for c in conversations if c.conversation_id == "CV-05-pricing-quote-wrong-corrected")
    claim = cv.turns[1].claims[0]
    live = world.rate(claim.pickup, claim.vehicle_class, claim.pickup_at.date())
    assert claim.quoted_price != live, "the 'wrong quote' fixture must actually differ from the world"
    assert claim.text_token in cv.expected.delivered_excludes


def test_sold_out_availability_claim_contradicts_the_world(conversations, world):
    cv = next(c for c in conversations if c.conversation_id == "CV-06-availability-sold-out")
    claim = cv.turns[1].claims[0]
    live = world.availability_count(claim.pickup, claim.vehicle_class, claim.pickup_at.date())
    assert live == 0
    assert claim.quoted_available is True
    assert cv.expected.delivered_excludes


def test_excluded_tokens_actually_appear_in_the_drafted_text(conversations):
    """A `delivered_excludes` entry only means something if the draft contains
    it — otherwise the assertion passes vacuously."""
    for cv in conversations:
        paths = [cv.turns] + [cv.turns[: br.from_turn] + br.turns for br in cv.branches]
        expectations = [cv.expected] + [br.expected for br in cv.branches]
        for turns, exp in zip(paths, expectations):
            drafted = " ".join(t.text for t in turns if t.speaker is Speaker.bot)
            for token in exp.delivered_excludes:
                assert token in drafted, f"{cv.conversation_id}: {token!r} never drafted"


# --- escalation & scope ----------------------------------------------------- #
@pytest.mark.parametrize(
    "conversation_id",
    ["CV-12-complaint-escalated", "CV-13-claim-dispute-escalated"],
)
def test_escalating_conversations_flag_handoff(conversations, conversation_id):
    cv = next(c for c in conversations if c.conversation_id == conversation_id)
    assert cv.expected.outcome is ConversationOutcome.escalated_to_human
    assert any(t.requests_handoff for t in cv.turns)


def test_escalation_paths_make_no_factual_claims(conversations):
    """The bot must not quote a refund or a price while escalating."""
    for cv in conversations:
        if cv.expected.outcome is not ConversationOutcome.escalated_to_human:
            continue
        assert not [c for t in cv.turns for c in t.claims], cv.conversation_id


def test_out_of_scope_declines_without_handoff(conversations):
    cv = next(c for c in conversations if c.intent is Intent.out_of_scope)
    assert cv.expected.outcome is ConversationOutcome.declined_out_of_scope
    assert not any(t.requests_handoff for t in cv.turns)


def test_ambiguous_opener_clarifies_before_quoting(conversations):
    cv = next(c for c in conversations if c.intent is Intent.ambiguous)
    assert not cv.turns[0].slots, "the ambiguous opener must not arrive pre-filled"
    first_claim_turn = next(t.turn for t in cv.turns if t.claims)
    clarifying_bot_turn = cv.turns[1]
    assert clarifying_bot_turn.speaker is Speaker.bot
    assert first_claim_turn > clarifying_bot_turn.turn, "quoted before clarifying"


# --- multi-turn state: the requirements-change case ------------------------- #
def test_requirements_change_overrides_slots_rather_than_accumulating(conversations):
    cv = next(c for c in conversations if c.intent is Intent.requirements_change)
    slots = final_slots(cv)
    assert slots["pickup"] == "MAN", "the later location must win"
    assert slots["vehicle_class"] == "SUV", "the later class must win"
    # dates were never changed, so they survive from the opener
    assert "pickup_at" in slots and "return_at" in slots


def test_requirements_change_revert_branch_restores_original_slots(conversations):
    cv = next(c for c in conversations if c.intent is Intent.requirements_change)
    slots = final_slots(cv, branch_id="revert")
    assert slots["pickup"] == "LHR"
    assert slots["vehicle_class"] == "ECAR"


def test_superseded_quote_must_not_be_delivered(conversations):
    cv = next(c for c in conversations if c.intent is Intent.requirements_change)
    assert cv.expected.delivered_excludes, "the superseded quote must be pinned"
    revert = next(b for b in cv.branches if b.branch_id == "revert")
    assert revert.expected.delivered_excludes
    # the two paths must exclude *different* quotes
    assert cv.expected.delivered_excludes != revert.expected.delivered_excludes


def test_branches_start_from_a_real_turn(conversations):
    for cv in conversations:
        for br in cv.branches:
            assert 1 <= br.from_turn <= len(cv.turns), f"{cv.conversation_id}/{br.branch_id}"
            assert br.turns, "an empty branch proves nothing"
            assert br.turns[0].turn == br.from_turn + 1


# --- the Phase-2 seam (§16.6) ----------------------------------------------- #
def test_scripted_reply_source_satisfies_the_protocol(conversations):
    src = ScriptedReplySource(conversations[0])
    assert isinstance(src, ReplySource)


def test_scripted_source_replays_customer_turns_in_order(conversations):
    cv = next(c for c in conversations if c.conversation_id == "CV-01-new-booking-slot-fill")
    src = ScriptedReplySource(cv)
    expected = [t.text for t in cv.turns if t.speaker is Speaker.customer]
    got = []
    for i in range(len(expected) + 1):
        reply = src.next_reply(ReplyContext(cv.conversation_id, cv.intent, i))
        if reply is None:
            break
        got.append(reply)
    assert got == expected


def test_scripted_source_follows_a_branch(conversations):
    cv = next(c for c in conversations if c.intent is Intent.requirements_change)
    src = ScriptedReplySource(cv)
    ctx = ReplyContext(cv.conversation_id, cv.intent, 2, branch_id="revert")
    assert "Heathrow" in src.next_reply(ctx)


def test_unknown_branch_is_an_error_not_a_silent_main_path(conversations):
    src = ScriptedReplySource(conversations[0])
    with pytest.raises(KeyError):
        src.next_reply(ReplyContext("x", Intent.new_booking, 0, branch_id="nope"))


def test_a_phase2_source_can_replace_the_scripted_one(conversations):
    """Proves the seam: an alternative source needs no change to the scenarios."""

    class StubLLMSource:
        def next_reply(self, ctx: ReplyContext) -> str | None:
            return None if ctx.turn_index > 1 else f"[generated {ctx.intent.value} #{ctx.turn_index}]"

    src: ReplySource = StubLLMSource()
    assert isinstance(src, ReplySource)
    assert src.next_reply(ReplyContext("c", Intent.new_booking, 0)).startswith("[generated")
    assert src.next_reply(ReplyContext("c", Intent.new_booking, 9)) is None
