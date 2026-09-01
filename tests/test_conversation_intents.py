"""S3 — scripted conversation trees (POA/16 §16.4 intents, §16.6 Phase-1 format).

What these prove, beyond "the objects validate":
  * all 17 mandated intents are covered, and stay covered;
  * every price/availability claim made in a conversation — on the main path or
    on a branch — is grounded in the SAME world the booking-API mock reads, so
    M10 can verify it for real;
  * `delivered_excludes` and `superseded_tokens` mean genuinely different
    things, and each token is checked against live data to prove it;
  * requirement changes mid-conversation override slots rather than accumulate;
  * both halves of the §16.6 Phase-2 seam — reply source and evaluator — are
    swappable.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from generator.intents import (
    Evaluator,
    ExactExpectationEvaluator,
    IntentScenarioComposer,
    ReplyContext,
    ReplySource,
    ScriptedReplySource,
    Verdict,
    all_turns,
    final_slots,
    path,
    paths,
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


def test_every_path_opens_with_the_customer_and_meets_its_bot_turn_floor(conversations):
    for cv in conversations:
        assert cv.turns[0].speaker is Speaker.customer, cv.conversation_id
        for branch_id, turns, exp in paths(cv):
            bot_turns = [t for t in turns if t.speaker is Speaker.bot]
            assert len(bot_turns) >= exp.min_bot_turns, f"{cv.conversation_id}/{branch_id}"


def test_turn_numbers_are_sequential_on_every_path(conversations):
    for cv in conversations:
        for branch_id, turns, _ in paths(cv):
            assert [t.turn for t in turns] == list(range(1, len(turns) + 1)), \
                f"{cv.conversation_id}/{branch_id}"


def test_only_customer_turns_carry_intent_only_bot_turns_carry_claims(conversations):
    for cv in conversations:
        for t in all_turns(cv):
            if t.speaker is Speaker.customer:
                assert t.intent is not None, f"{cv.conversation_id} turn {t.turn}"
                assert not t.claims, f"{cv.conversation_id} turn {t.turn}"
            else:
                assert t.intent is None, f"{cv.conversation_id} turn {t.turn}"


# --- world grounding (the point of the whole dataset) ----------------------- #
def test_every_price_claim_is_grounded_in_the_world(conversations, world):
    """A quoted price must correspond to a real (location, class, date) the
    booking API can be asked about — otherwise M10 could never verify it.
    Branch turns carry claims too, so `all_turns` rather than `cv.turns`."""
    seen = 0
    for cv in conversations:
        for t in all_turns(cv):
            for claim in t.claims:
                if claim.kind is not ClaimKind.price:
                    continue
                live = world.rate(claim.pickup, claim.vehicle_class, claim.pickup_at.date())
                assert isinstance(live, Decimal)
                seen += 1
    assert seen >= 6, "expected several verifiable price claims across the trees"


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


def test_pinned_tokens_actually_appear_in_the_drafted_text(conversations):
    """A pinned token only means something if the draft contains it — otherwise
    the assertion passes vacuously."""
    for cv in conversations:
        for branch_id, turns, exp in paths(cv):
            drafted = " ".join(t.text for t in turns if t.speaker is Speaker.bot)
            for token in exp.delivered_excludes + exp.superseded_tokens:
                assert token in drafted, f"{cv.conversation_id}/{branch_id}: {token!r} never drafted"


def test_the_two_exclusion_fields_mean_different_things(conversations, world):
    """`delivered_excludes` tokens must be WRONG against the world (M10 strips
    them, so they must never be delivered). `superseded_tokens` must be RIGHT —
    they were legitimately delivered and only later obsoleted by the customer.

    This is the discriminator a runner needs: conflating the two makes one of
    the two cases silently stop testing anything.
    """
    def claim_for(cv, token):
        for t in all_turns(cv):
            for c in t.claims:
                if c.text_token == token:
                    return c
        raise AssertionError(f"{cv.conversation_id}: no claim for {token!r}")

    checked = 0
    for cv in conversations:
        for _branch_id, _turns, exp in paths(cv):
            for token in exp.delivered_excludes:
                c = claim_for(cv, token)
                if c.kind is ClaimKind.price:
                    live = world.rate(c.pickup, c.vehicle_class, c.pickup_at.date())
                    assert c.quoted_price != live, f"{token!r} is not actually wrong"
                else:
                    live_n = world.availability_count(c.pickup, c.vehicle_class, c.pickup_at.date())
                    assert c.quoted_available is not (live_n > 0), f"{token!r} is not actually wrong"
                checked += 1
            for token in exp.superseded_tokens:
                c = claim_for(cv, token)
                assert c.kind is ClaimKind.price
                live = world.rate(c.pickup, c.vehicle_class, c.pickup_at.date())
                assert c.quoted_price == live, f"{token!r} should have been correct when said"
                checked += 1
    assert checked >= 4


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
        for branch_id, turns, exp in paths(cv):
            if exp.outcome is not ConversationOutcome.escalated_to_human:
                continue
            assert not [c for t in turns for c in t.claims], f"{cv.conversation_id}/{branch_id}"


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


def test_superseded_quote_is_pinned_as_superseded_not_as_unverified(conversations):
    cv = next(c for c in conversations if c.intent is Intent.requirements_change)
    revert = next(b for b in cv.branches if b.branch_id == "revert")
    assert cv.expected.superseded_tokens, "the superseded quote must be pinned"
    assert revert.expected.superseded_tokens
    # ...and NOT as delivered_excludes: these quotes were correct when said.
    assert not cv.expected.delivered_excludes
    assert not revert.expected.delivered_excludes
    # the two paths supersede *different* quotes
    assert cv.expected.superseded_tokens != revert.expected.superseded_tokens


def test_branches_start_from_a_real_turn(conversations):
    for cv in conversations:
        for br in cv.branches:
            assert 1 <= br.from_turn <= len(cv.turns), f"{cv.conversation_id}/{br.branch_id}"
            assert br.turns, "an empty branch proves nothing"
            assert br.turns[0].turn == br.from_turn + 1


# --- the Phase-2 seam, half 1: reply source (§16.6) ------------------------- #
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


def test_path_and_reply_source_agree_on_every_branch(conversations):
    """One definition of 'the path' — guards against a second implementation
    drifting from `path()`."""
    for cv in conversations:
        for branch_id, turns, _ in paths(cv):
            src = ScriptedReplySource(cv)
            want = [t.text for t in turns if t.speaker is Speaker.customer]
            got = [
                src.next_reply(ReplyContext(cv.conversation_id, cv.intent, i, branch_id=branch_id))
                for i in range(len(want))
            ]
            assert got == want, f"{cv.conversation_id}/{branch_id}"


def test_unknown_branch_is_an_error_not_a_silent_main_path(conversations):
    src = ScriptedReplySource(conversations[0])
    with pytest.raises(KeyError):
        src.next_reply(ReplyContext("x", Intent.new_booking, 0, branch_id="nope"))


def test_a_phase2_source_can_replace_the_scripted_one():
    """Proves the seam: an alternative source needs no change to the scenarios."""

    class StubLLMSource:
        def next_reply(self, ctx: ReplyContext) -> str | None:
            return None if ctx.turn_index > 1 else f"[generated {ctx.intent.value} #{ctx.turn_index}]"

    src: ReplySource = StubLLMSource()
    assert isinstance(src, ReplySource)
    assert src.next_reply(ReplyContext("c", Intent.new_booking, 0)).startswith("[generated")
    assert src.next_reply(ReplyContext("c", Intent.new_booking, 9)) is None


# --- the Phase-2 seam, half 2: evaluator (§16.6 pass/fail) ------------------ #
def test_exact_evaluator_passes_the_scripted_paths(conversations):
    """Every path except the deliberately-unverified ones must pass as scripted."""
    ev = ExactExpectationEvaluator()
    assert isinstance(ev, Evaluator)
    unverified = {"CV-05-pricing-quote-wrong-corrected", "CV-06-availability-sold-out"}
    for cv in conversations:
        if cv.conversation_id in unverified:
            continue
        for branch_id, turns, exp in paths(cv):
            verdict = ev.evaluate(cv, turns, exp)
            assert verdict.passed, f"{cv.conversation_id}/{branch_id}: {verdict.reasons}"


def test_evaluator_fails_when_an_unverified_quote_is_delivered(conversations):
    """The raw CV-05 script still contains the wrong quote — that is the point:
    M10 must strip it before delivery, so the un-verified transcript must fail."""
    cv = next(c for c in conversations if c.conversation_id == "CV-05-pricing-quote-wrong-corrected")
    verdict = ExactExpectationEvaluator().evaluate(cv, path(cv), cv.expected)
    assert not verdict.passed
    assert any("unverified" in r for r in verdict.reasons)


def test_evaluator_fails_when_a_superseded_quote_survives_to_the_end(conversations):
    cv = next(c for c in conversations if c.intent is Intent.requirements_change)
    turns = path(cv)
    stale = cv.expected.superseded_tokens[0]
    tampered = list(turns[:-1]) + [turns[-1].model_copy(update={"text": f"Booked at {stale}/day."})]
    verdict = ExactExpectationEvaluator().evaluate(cv, tampered, cv.expected)
    assert not verdict.passed
    assert any("superseded" in r for r in verdict.reasons)


def test_superseded_token_earlier_in_the_transcript_is_fine(conversations):
    """It was correct when said — only the FINAL turn must be clean."""
    cv = next(c for c in conversations if c.intent is Intent.requirements_change)
    stale = cv.expected.superseded_tokens[0]
    drafted = " ".join(t.text for t in path(cv) if t.speaker is Speaker.bot)
    assert stale in drafted, "the superseded quote is delivered mid-conversation by design"
    assert ExactExpectationEvaluator().evaluate(cv, path(cv), cv.expected).passed


def test_evaluator_fails_on_a_missing_required_slot(conversations):
    cv = next(c for c in conversations if c.conversation_id == "CV-01-new-booking-slot-fill")
    stripped = [t.model_copy(update={"slots": {}}) for t in path(cv)]
    verdict = ExactExpectationEvaluator().evaluate(cv, stripped, cv.expected)
    assert not verdict.passed
    assert any("required slot" in r for r in verdict.reasons)


def test_a_phase2_evaluator_can_replace_the_exact_one():
    class StubJudge:
        def evaluate(self, scenario, transcript, expected) -> Verdict:
            return Verdict(passed=True, reasons=[])

    ev: Evaluator = StubJudge()
    assert isinstance(ev, Evaluator)
