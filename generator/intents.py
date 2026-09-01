"""Scripted conversation trees — the inbound-conversation test driver (S3).

Implements POA/16 §16.4 (the 17 mandated conversation intents) and §16.6
(*scripted* trees for Phase 1, deterministic and regression-friendly).

Two things this module deliberately gets right for Phase 2:

1. **The customer-reply source is a protocol**, not a hard-coded list. Phase 1
   ships `ScriptedReplySource`; Phase 2 drops in an LLM-backed source that
   varies wording, tone, typos and ambiguity while preserving intent — with no
   change to scenarios or to whatever runner consumes them (§16.6).
2. **Claims are grounded in the same `World`** the booking-API mock reads, so a
   conversation that quotes a price is verifiable by M10 exactly like the
   proactive golden scenarios are. A wrong quote is wrong against real data,
   not against a hard-coded string.

The composer is intent-*complete* (all 17) but not uniform in depth: coverage
comes from structural distinctness — single-turn lookup, multi-turn slot
filling, out-of-scope refusal, ambiguity/clarification, and the mid-conversation
requirement change that §16.4 calls out by name and that is the only one which
genuinely stresses multi-turn state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol, runtime_checkable

from .models import (
    BookingClaim,
    ClaimKind,
    ConversationBranch,
    ConversationExpected,
    ConversationOutcome,
    ConversationScenario,
    ConversationTurn,
    Intent,
    Speaker,
)
from .world import World

_TZ = timezone.utc


# --------------------------------------------------------------------------- #
# Reply source — the Phase-1 / Phase-2 seam (§16.6)
# --------------------------------------------------------------------------- #
@dataclass
class ReplyContext:
    """What a reply source is allowed to see when producing the next customer turn."""
    conversation_id: str
    intent: Intent
    turn_index: int
    history: list[ConversationTurn] = field(default_factory=list)
    branch_id: str | None = None


@runtime_checkable
class ReplySource(Protocol):
    """Produces the customer's next utterance. Returns None when the script ends.

    Phase 1: `ScriptedReplySource` (deterministic).
    Phase 2: an LLM-backed source with this same shape — the runner and the
    scenarios do not change. Keep this signature stable.
    """

    def next_reply(self, ctx: ReplyContext) -> str | None: ...


class ScriptedReplySource:
    """Phase-1 source: replays the customer turns of a scenario (or a branch)."""

    def __init__(self, scenario: ConversationScenario) -> None:
        self._scenario = scenario

    def _path(self, branch_id: str | None) -> list[ConversationTurn]:
        if branch_id is None:
            return self._scenario.turns
        for br in self._scenario.branches:
            if br.branch_id == branch_id:
                head = [t for t in self._scenario.turns if t.turn <= br.from_turn]
                return head + br.turns
        raise KeyError(f"no branch {branch_id!r} in {self._scenario.conversation_id}")

    def next_reply(self, ctx: ReplyContext) -> str | None:
        customer_turns = [t for t in self._path(ctx.branch_id) if t.speaker is Speaker.customer]
        if ctx.turn_index >= len(customer_turns):
            return None
        return customer_turns[ctx.turn_index].text


# --------------------------------------------------------------------------- #
# Composer
# --------------------------------------------------------------------------- #
class IntentScenarioComposer:
    """Builds one scripted conversation tree per POA/16 §16.4 intent."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.day: date = world.start
        self.pickup_at = datetime(self.day.year, self.day.month, self.day.day, 10, 0, tzinfo=_TZ)
        self.return_at = self.pickup_at + timedelta(days=3)

    # helpers ------------------------------------------------------------ #
    def _money(self, loc: str, amount: Decimal) -> str:
        sym = {"GBP": "£", "EUR": "€"}.get(self.world.currency(loc), "")
        return f"{sym}{amount:.2f}"

    def _price_claim(self, loc: str, vc: str, quoted: Decimal, token: str) -> BookingClaim:
        return BookingClaim(
            kind=ClaimKind.price,
            pickup=loc,
            dropoff=loc,
            pickup_at=self.pickup_at,
            return_at=self.return_at,
            vehicle_class=vc,
            quoted_price=quoted,
            text_token=token,
        )

    def _availability_claim(self, loc: str, vc: str, available: bool, token: str) -> BookingClaim:
        return BookingClaim(
            kind=ClaimKind.availability,
            pickup=loc,
            dropoff=loc,
            pickup_at=self.pickup_at,
            return_at=self.return_at,
            vehicle_class=vc,
            quoted_available=available,
            text_token=token,
        )

    @staticmethod
    def _cust(turn: int, text: str, intent: Intent, **slots: str) -> ConversationTurn:
        return ConversationTurn(
            turn=turn, speaker=Speaker.customer, text=text, intent=intent, slots=slots
        )

    @staticmethod
    def _bot(turn: int, text: str, claims=None, handoff: bool = False) -> ConversationTurn:
        return ConversationTurn(
            turn=turn,
            speaker=Speaker.bot,
            text=text,
            claims=list(claims or []),
            requests_handoff=handoff,
        )

    def _find_zero_availability(self) -> tuple[str, str]:
        for a in self.world.availability:
            if a.date == self.day and a.available == 0:
                return a.location_id, a.vehicle_class
        return "LHR", "SUV"

    # ------------------------------------------------------------------ #
    def all(self) -> list[ConversationScenario]:
        convs = [
            self.cv01_new_booking(),
            self.cv02_existing_booking_lookup(),
            self.cv03_booking_modification(),
            self.cv04_cancellation(),
            self.cv05_pricing_quote(),
            self.cv06_vehicle_availability(),
            self.cv07_vehicle_class_question(),
            self.cv08_pickup_dropoff_question(),
            self.cv09_fees_and_charges(),
            self.cv10_extras_insurance(),
            self.cv11_payment_deposit(),
            self.cv12_complaint(),
            self.cv13_claim_dispute(),
            self.cv14_general_info(),
            self.cv15_out_of_scope(),
            self.cv16_ambiguous(),
            self.cv17_requirements_change(),
        ]
        covered = {c.intent for c in convs}
        missing = set(Intent) - covered
        if missing:  # guards against a tree being dropped in a future edit
            raise AssertionError(f"intents not covered: {sorted(i.value for i in missing)}")
        return convs

    # 1 — new booking: multi-turn slot filling, ends in a verified quote ---- #
    def cv01_new_booking(self) -> ConversationScenario:
        loc, vc = "LHR", "ICAR"
        rate = self.world.rate(loc, vc, self.day)
        token = self._money(loc, rate)
        return ConversationScenario(
            conversation_id="CV-01-new-booking-slot-fill",
            intent=Intent.new_booking,
            description="Slot-filling across turns: location, dates, class — then a live-verified quote.",
            seed=201,
            customer_id="hfb-cust-cv01",
            turns=[
                self._cust(1, "I need to hire a car next week", Intent.new_booking),
                self._bot(2, "Happy to help — which airport or city are you collecting from?"),
                self._cust(3, "London Heathrow", Intent.new_booking, pickup=loc),
                self._bot(4, "Got it. What dates do you need it, and what size of vehicle?"),
                self._cust(
                    5, "Pick up the 1st, back on the 4th — something mid-size",
                    Intent.new_booking, pickup_at=self.pickup_at.isoformat(),
                    return_at=self.return_at.isoformat(), vehicle_class=vc,
                ),
                self._bot(
                    6, f"An Intermediate at London Heathrow is {token}/day for those dates. Shall I book it?",
                    claims=[self._price_claim(loc, vc, rate, token)],
                ),
                self._cust(7, "yes please, book it", Intent.new_booking),
                self._bot(8, "Booked — confirmation is on its way to your account email."),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.booking_created,
                required_slots=["pickup", "pickup_at", "return_at", "vehicle_class"],
                min_bot_turns=4,
            ),
        )

    # 2 — existing booking lookup: single-turn retrieval -------------------- #
    def cv02_existing_booking_lookup(self) -> ConversationScenario:
        return ConversationScenario(
            conversation_id="CV-02-existing-booking-lookup",
            intent=Intent.existing_booking_lookup,
            description="Shortest path: reference supplied up front, one retrieval, done.",
            seed=202,
            customer_id="hfb-cust-cv02",
            turns=[
                self._cust(
                    1, "Can you pull up my booking HFB-000123?",
                    Intent.existing_booking_lookup, booking_ref="HFB-000123",
                ),
                self._bot(
                    2,
                    "That's an Intermediate from London Heathrow, collecting 1 Sep at 10:00 and "
                    "returning 4 Sep. Anything you'd like to change?",
                ),
                self._cust(3, "no that's all, thanks", Intent.existing_booking_lookup),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.resolved,
                required_slots=["booking_ref"],
            ),
        )

    # 3 — modification: changes a booking, re-quotes, re-verifies ----------- #
    def cv03_booking_modification(self) -> ConversationScenario:
        loc, vc = "LHR", "FCAR"
        rate = self.world.rate(loc, vc, self.day)
        token = self._money(loc, rate)
        return ConversationScenario(
            conversation_id="CV-03-booking-modification-upsize",
            intent=Intent.booking_modification,
            description="Upsize an existing booking; the new rate is quoted and must be verified.",
            seed=203,
            customer_id="hfb-cust-cv03",
            turns=[
                self._cust(
                    1, "I need a bigger car on booking HFB-000124",
                    Intent.booking_modification, booking_ref="HFB-000124",
                ),
                self._bot(2, "Of course — a Full-size is the next step up. Shall I price that?"),
                self._cust(3, "yes go on", Intent.booking_modification, vehicle_class=vc),
                self._bot(
                    4, f"Full-size comes to {token}/day. Want me to switch it over?",
                    claims=[self._price_claim(loc, vc, rate, token)],
                ),
                self._cust(5, "do it", Intent.booking_modification),
                self._bot(6, "Updated — your booking is now a Full-size."),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.booking_modified,
                required_slots=["booking_ref", "vehicle_class"],
                min_bot_turns=3,
            ),
        )

    # 4 — cancellation: policy-gated, with a branch that keeps the booking -- #
    def cv04_cancellation(self) -> ConversationScenario:
        return ConversationScenario(
            conversation_id="CV-04-cancellation-with-retain-branch",
            intent=Intent.cancellation,
            description="Cancel request; main path cancels, branch retains after the fee is explained.",
            seed=204,
            customer_id="hfb-cust-cv04",
            turns=[
                self._cust(
                    1, "I want to cancel HFB-000125",
                    Intent.cancellation, booking_ref="HFB-000125",
                ),
                self._bot(
                    2,
                    "I can do that. You're inside the free-cancellation window, so there's no charge. "
                    "Shall I go ahead?",
                ),
                self._cust(3, "yes cancel it", Intent.cancellation),
                self._bot(4, "Cancelled. You'll get a confirmation shortly."),
            ],
            branches=[
                ConversationBranch(
                    branch_id="retain",
                    description="Customer changes their mind once the terms are clear.",
                    from_turn=2,
                    turns=[
                        self._cust(3, "actually no, leave it as it is", Intent.cancellation),
                        self._bot(4, "No problem — your booking is unchanged."),
                    ],
                    expected=ConversationExpected(
                        outcome=ConversationOutcome.resolved,
                        required_slots=["booking_ref"],
                    ),
                )
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.booking_cancelled,
                required_slots=["booking_ref"],
            ),
        )

    # 5 — pricing quote: WRONG quote, must be corrected before delivery ----- #
    def cv05_pricing_quote(self) -> ConversationScenario:
        loc, vc = "MAN", "ECAR"
        rate = self.world.rate(loc, vc, self.day)
        wrong = (rate - Decimal("7.50")).quantize(Decimal("0.01"))
        wrong_token = self._money(loc, wrong)
        return ConversationScenario(
            conversation_id="CV-05-pricing-quote-wrong-corrected",
            intent=Intent.pricing_quote,
            description="Bot under-quotes against the live world; M10 must correct it — the wrong "
                        "figure must never reach the customer.",
            seed=205,
            customer_id="hfb-cust-cv05",
            turns=[
                self._cust(
                    1, "How much is an Economy at Manchester for three days?",
                    Intent.pricing_quote, pickup=loc, vehicle_class=vc,
                ),
                self._bot(
                    2, f"That's {wrong_token}/day.",
                    claims=[self._price_claim(loc, vc, wrong, wrong_token)],
                ),
                self._cust(3, "ok thanks", Intent.pricing_quote),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.resolved,
                required_slots=["pickup", "vehicle_class"],
                delivered_excludes=[wrong_token],
            ),
        )

    # 6 — availability: sold out, offer alternative ------------------------- #
    def cv06_vehicle_availability(self) -> ConversationScenario:
        loc, vc = self._find_zero_availability()
        token = "available"
        return ConversationScenario(
            conversation_id="CV-06-availability-sold-out",
            intent=Intent.vehicle_availability,
            description="Requested class has zero availability in the world; a positive claim must "
                        "be corrected, not delivered.",
            seed=206,
            customer_id="hfb-cust-cv06",
            turns=[
                self._cust(
                    1, "Do you have anything for pickup tomorrow?",
                    Intent.vehicle_availability, pickup=loc, vehicle_class=vc,
                ),
                self._bot(
                    2, f"Yes, that class is {token} at your location.",
                    claims=[self._availability_claim(loc, vc, True, token)],
                ),
                self._cust(3, "ok", Intent.vehicle_availability),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.resolved,
                required_slots=["pickup", "vehicle_class"],
                delivered_excludes=[token],
            ),
        )

    # 7 — vehicle class question: catalogue lookup, no claim ---------------- #
    def cv07_vehicle_class_question(self) -> ConversationScenario:
        return ConversationScenario(
            conversation_id="CV-07-vehicle-class-question",
            intent=Intent.vehicle_class_question,
            description="Pure catalogue question — no price, no availability, so nothing to verify.",
            seed=207,
            customer_id="hfb-cust-cv07",
            turns=[
                self._cust(
                    1, "What's the difference between Intermediate and Full-size?",
                    Intent.vehicle_class_question, vehicle_class="ICAR",
                ),
                self._bot(
                    2,
                    "Intermediate seats five with two large cases; Full-size gives you more boot "
                    "space and a bigger engine. Both are automatic.",
                ),
                self._cust(3, "got it, thanks", Intent.vehicle_class_question),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.resolved,
                required_slots=["vehicle_class"],
            ),
        )

    # 8 — pickup/drop-off question, incl. one-way -------------------------- #
    def cv08_pickup_dropoff_question(self) -> ConversationScenario:
        return ConversationScenario(
            conversation_id="CV-08-pickup-dropoff-one-way",
            intent=Intent.pickup_dropoff_question,
            description="Station logistics + a one-way request (POA/16 §16.2 adds one-way bookings).",
            seed=208,
            customer_id="hfb-cust-cv08",
            turns=[
                self._cust(
                    1, "Can I collect at Heathrow and drop off in Manchester?",
                    Intent.pickup_dropoff_question, pickup="LHR", dropoff="MAN",
                ),
                self._bot(
                    2,
                    "Yes — that's a one-way rental. There's a one-way fee and the drop-off desk "
                    "closes at 23:00. Want me to price it?",
                ),
                self._cust(3, "not yet, just checking", Intent.pickup_dropoff_question),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.resolved,
                required_slots=["pickup", "dropoff"],
            ),
        )

    # 9 — fees and charges (POA/16 §16.1 late return / no-show / fuel) ------ #
    def cv09_fees_and_charges(self) -> ConversationScenario:
        return ConversationScenario(
            conversation_id="CV-09-fees-late-return",
            intent=Intent.fees_and_charges,
            description="Late-return charge explained from the fee rules, not invented.",
            seed=209,
            customer_id="hfb-cust-cv09",
            turns=[
                self._cust(
                    1, "What happens if I bring it back a day late?",
                    Intent.fees_and_charges, fee_type="late_return",
                ),
                self._bot(
                    2,
                    "There's a grace period of 29 minutes; past that you're charged a further "
                    "rental day at the standard rate, plus a late-return fee.",
                ),
                self._cust(3, "and if I don't turn up at all?", Intent.fees_and_charges, fee_type="no_show"),
                self._bot(4, "A no-show is charged at one rental day and the booking is released."),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.resolved,
                required_slots=["fee_type"],
                min_bot_turns=2,
            ),
        )

    # 10 — extras & insurance ---------------------------------------------- #
    def cv10_extras_insurance(self) -> ConversationScenario:
        return ConversationScenario(
            conversation_id="CV-10-extras-insurance",
            intent=Intent.extras_insurance,
            description="Protection-product and extras catalogue question, then adds one.",
            seed=210,
            customer_id="hfb-cust-cv10",
            turns=[
                self._cust(
                    1, "Do I need the excess cover, and can I add a child seat?",
                    Intent.extras_insurance, product="excess_protection", extra="child_seat",
                ),
                self._bot(
                    2,
                    "Excess protection is optional — it reduces your damage excess to zero. A child "
                    "seat is bookable per day, subject to availability at the desk.",
                ),
                self._cust(3, "add the child seat to HFB-000126", Intent.extras_insurance, booking_ref="HFB-000126"),
                self._bot(4, "Added — it'll show on your updated confirmation."),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.booking_modified,
                required_slots=["extra", "booking_ref"],
                min_bot_turns=2,
            ),
        )

    # 11 — payment & deposit ------------------------------------------------ #
    def cv11_payment_deposit(self) -> ConversationScenario:
        return ConversationScenario(
            conversation_id="CV-11-payment-deposit",
            intent=Intent.payment_deposit,
            description="Deposit/hold question — answered from policy, never asks for card data.",
            seed=211,
            customer_id="hfb-cust-cv11",
            turns=[
                self._cust(
                    1, "How much deposit do you take at the desk?",
                    Intent.payment_deposit, topic="deposit",
                ),
                self._bot(
                    2,
                    "We place a hold on the driver's credit card at collection — the amount depends "
                    "on vehicle class. It's released after the car comes back. Please don't share "
                    "card details in chat.",
                ),
                self._cust(3, "understood", Intent.payment_deposit),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.resolved,
                required_slots=["topic"],
            ),
        )

    # 12 — complaint: escalates to a human --------------------------------- #
    def cv12_complaint(self) -> ConversationScenario:
        return ConversationScenario(
            conversation_id="CV-12-complaint-escalated",
            intent=Intent.complaint,
            description="Service complaint the bot must not attempt to settle — straight to M07.",
            seed=212,
            customer_id="hfb-cust-cv12",
            turns=[
                self._cust(
                    1, "The car I got was filthy and nobody at the desk cared.",
                    Intent.complaint, booking_ref="HFB-000127", severity="high",
                ),
                self._bot(
                    2,
                    "I'm sorry — that's not the standard we aim for. I'm putting you through to a "
                    "colleague who can look at compensation.",
                    handoff=True,
                ),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.escalated_to_human,
                required_slots=["booking_ref"],
            ),
        )

    # 13 — claim / billing dispute: escalates, quotes nothing --------------- #
    def cv13_claim_dispute(self) -> ConversationScenario:
        return ConversationScenario(
            conversation_id="CV-13-claim-dispute-escalated",
            intent=Intent.claim_dispute,
            description="Disputed charge — the bot must not concede or quote a refund amount.",
            seed=213,
            customer_id="hfb-cust-cv13",
            turns=[
                self._cust(
                    1, "I've been charged a fuel fee but I returned it full.",
                    Intent.claim_dispute, booking_ref="HFB-000128", fee_type="fuel",
                ),
                self._bot(
                    2,
                    "I can see the charge on that booking. Disputes are handled by our billing team "
                    "— I'm passing this to them with your booking reference.",
                    handoff=True,
                ),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.escalated_to_human,
                required_slots=["booking_ref", "fee_type"],
            ),
        )

    # 14 — general info ----------------------------------------------------- #
    def cv14_general_info(self) -> ConversationScenario:
        return ConversationScenario(
            conversation_id="CV-14-general-info",
            intent=Intent.general_info,
            description="Policy question with no booking attached.",
            seed=214,
            customer_id="hfb-cust-cv14",
            turns=[
                self._cust(
                    1, "What's the minimum age to rent with you?",
                    Intent.general_info, topic="driver_age",
                ),
                self._bot(
                    2,
                    "The minimum is 21 for most classes, and drivers under 25 pay a young-driver "
                    "surcharge. Premium classes may require 25+.",
                ),
                self._cust(3, "thanks", Intent.general_info),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.resolved,
                required_slots=["topic"],
            ),
        )

    # 15 — out of scope: declines, does not improvise ---------------------- #
    def cv15_out_of_scope(self) -> ConversationScenario:
        return ConversationScenario(
            conversation_id="CV-15-out-of-scope-declined",
            intent=Intent.out_of_scope,
            description="Unrelated request — the bot declines and redirects rather than answering.",
            seed=215,
            customer_id="hfb-cust-cv15",
            turns=[
                self._cust(1, "Can you book me a hotel in Paris too?", Intent.out_of_scope),
                self._bot(
                    2,
                    "I can only help with vehicle rental, I'm afraid — I can't book hotels. Anything "
                    "I can do on the car side?",
                ),
                self._cust(3, "no, never mind", Intent.out_of_scope),
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.declined_out_of_scope,
            ),
        )

    # 16 — ambiguous: must clarify before acting --------------------------- #
    def cv16_ambiguous(self) -> ConversationScenario:
        loc, vc = "LHR", "ECAR"
        rate = self.world.rate(loc, vc, self.day)
        token = self._money(loc, rate)
        return ConversationScenario(
            conversation_id="CV-16-ambiguous-clarify-first",
            intent=Intent.ambiguous,
            description="Under-specified opener: the bot must ask, not guess. Branch = customer "
                        "abandons instead of clarifying.",
            seed=216,
            customer_id="hfb-cust-cv16",
            turns=[
                self._cust(1, "how much is it", Intent.ambiguous),
                self._bot(2, "Happy to check — which location and vehicle class did you have in mind?"),
                self._cust(
                    3, "Heathrow, the cheapest one", Intent.ambiguous,
                    pickup=loc, vehicle_class=vc,
                ),
                self._bot(
                    4, f"Economy at London Heathrow is {token}/day.",
                    claims=[self._price_claim(loc, vc, rate, token)],
                ),
            ],
            branches=[
                ConversationBranch(
                    branch_id="abandon",
                    description="Customer never supplies the missing slots.",
                    from_turn=2,
                    turns=[self._cust(3, "never mind", Intent.ambiguous)],
                    expected=ConversationExpected(
                        outcome=ConversationOutcome.abandoned,
                        required_slots=[],
                    ),
                )
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.resolved,
                required_slots=["pickup", "vehicle_class"],
                min_bot_turns=2,
            ),
        )

    # 17 — requirements change mid-conversation (§16.4, named explicitly) --- #
    def cv17_requirements_change(self) -> ConversationScenario:
        """The multi-turn state stress case.

        The customer supplies a full slot set, then changes *two* of them after a
        quote has already been given. The old quote must not survive, and the
        superseded slot values must not be carried into the booking — which is
        why both old tokens are in `delivered_excludes`.
        """
        loc_a, vc_a = "LHR", "ECAR"
        loc_b, vc_b = "MAN", "SUV"
        rate_a = self.world.rate(loc_a, vc_a, self.day)
        rate_b = self.world.rate(loc_b, vc_b, self.day)
        token_a = self._money(loc_a, rate_a)
        token_b = self._money(loc_b, rate_b)
        return ConversationScenario(
            conversation_id="CV-17-requirements-change-midway",
            intent=Intent.requirements_change,
            description="Customer changes location AND vehicle class after a quote; the superseded "
                        "quote must be dropped and the booking must use the new slots.",
            seed=217,
            customer_id="hfb-cust-cv17",
            turns=[
                self._cust(
                    1, "Economy from Heathrow, 1st to the 4th please",
                    Intent.new_booking, pickup=loc_a, vehicle_class=vc_a,
                    pickup_at=self.pickup_at.isoformat(), return_at=self.return_at.isoformat(),
                ),
                self._bot(
                    2, f"Economy at London Heathrow is {token_a}/day. Shall I book it?",
                    claims=[self._price_claim(loc_a, vc_a, rate_a, token_a)],
                ),
                self._cust(
                    3, "actually, make it Manchester instead — and I need an SUV, not an Economy",
                    Intent.requirements_change, pickup=loc_b, vehicle_class=vc_b,
                ),
                self._bot(
                    4, f"No problem — an SUV at Manchester is {token_b}/day for those dates.",
                    claims=[self._price_claim(loc_b, vc_b, rate_b, token_b)],
                ),
                self._cust(5, "yes that one", Intent.requirements_change),
                self._bot(6, "Booked — an SUV collecting at Manchester."),
            ],
            branches=[
                ConversationBranch(
                    branch_id="revert",
                    description="Customer reverts to the original requirements after seeing the new price.",
                    from_turn=4,
                    turns=[
                        self._cust(
                            5, "that's more than I thought — go back to the Heathrow Economy",
                            Intent.requirements_change, pickup=loc_a, vehicle_class=vc_a,
                        ),
                        self._bot(
                            6, f"Back to Economy at London Heathrow, {token_a}/day. Booking that now.",
                            claims=[self._price_claim(loc_a, vc_a, rate_a, token_a)],
                        ),
                    ],
                    expected=ConversationExpected(
                        outcome=ConversationOutcome.booking_created,
                        required_slots=["pickup", "vehicle_class"],
                        delivered_excludes=[token_b],  # the superseded SUV quote
                        min_bot_turns=3,
                    ),
                )
            ],
            expected=ConversationExpected(
                outcome=ConversationOutcome.booking_created,
                required_slots=["pickup", "vehicle_class", "pickup_at", "return_at"],
                delivered_excludes=[token_a],  # the superseded Heathrow Economy quote
                min_bot_turns=3,
            ),
        )


def final_slots(scenario: ConversationScenario, branch_id: str | None = None) -> dict[str, str]:
    """Slots as they stand at the end of a path — later turns override earlier.

    This is the rule M08/M12 must implement for CV-17: a requirement change
    *replaces* a slot rather than adding a second value for it.
    """
    turns = ScriptedReplySource(scenario)._path(branch_id)
    slots: dict[str, str] = {}
    for t in turns:
        slots.update(t.slots)
    return slots
