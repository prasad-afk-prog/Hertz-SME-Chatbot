"""Resolution / stuck detection (M12 node AH) and booking attribution (AJ).

**Resolution is a judgement the bot makes about itself, so it errs toward
handing over.** §3.1 lists three signals — intent classification, explicit
customer confirmation, and a bounded max-turns / stuck detector. Getting this
wrong in the "resolved" direction strands a customer who still needs help;
getting it wrong the other way costs an agent's time. The second is cheaper, so
`Verdict.unresolved` is the default and `resolved` has to be earned.

Some intents are **never** bot-resolvable regardless of what the customer says.
Complaints and billing disputes go to a person — the S3 conversation trees
(`CV-12`, `CV-13`) already pin that expectation, and this is where it becomes
behaviour.

The terminal decision itself delegates to `reference.terminal_state`, the
executable spec `test_golden_scenarios.py` asserts against via the AE/AH
branches. Everything here layers around that call rather than replacing it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from generator.models import Intent

from .handoff import HandoffReason


class Verdict(str, Enum):
    resolved = "resolved"        # the bot helped; surface the deep link (AI)
    unresolved = "unresolved"    # keep going, if turns remain
    stuck = "stuck"              # escalate now — more turns will not help


#: Customer phrasings that settle it. Explicit confirmation is the strongest
#: signal §3.1 lists, and it is the only one that alone means "resolved".
_CONFIRMED = (
    "yes please", "yes thanks", "that's great", "thats great", "perfect",
    "sorted", "all good", "that works", "book it", "go ahead", "do it",
    "thank you", "thanks, that", "great, thanks", "ja, gerne", "oui merci",
    "sí gracias", "si gracias",
)

#: Phrasings that mean the bot is not getting there.
_STUCK = (
    "you're not listening", "youre not listening", "that's not what i asked",
    "thats not what i asked", "this is wrong", "no, i said", "i already told you",
    "speak to someone", "talk to a human", "real person", "an agent",
    "this isn't helping", "this isnt helping", "useless",
)

#: Explicit asks for a person — honoured immediately, whatever else is happening.
_WANTS_HUMAN = (
    "speak to someone", "talk to a human", "real person", "an agent",
    "customer service", "put me through",
)

#: Intents a bot must never claim to have resolved.
NEVER_BOT_RESOLVABLE = {
    Intent.complaint: HandoffReason.complaint,
    Intent.claim_dispute: HandoffReason.claim_dispute,
}


def _matches(text: str, phrases) -> bool:
    lowered = text.lower()
    return any(p in lowered for p in phrases)


@dataclass
class ResolutionOutcome:
    verdict: Verdict
    reason: HandoffReason | None = None     # set when the verdict escalates
    evidence: str | None = None             # what tipped it, for the audit log


class ResolutionDetector:
    """AH. Conservative by construction — see the module docstring."""

    def assess(
        self,
        customer_text: str,
        *,
        intent: Intent | None = None,
        turns_used: int = 1,
        max_turns: int = 4,
        claim_unverifiable: bool = False,
    ) -> ResolutionOutcome:
        # 1. An explicit request for a person outranks everything.
        if _matches(customer_text, _WANTS_HUMAN):
            return ResolutionOutcome(
                Verdict.stuck, HandoffReason.customer_requested, "customer asked for a person"
            )

        # 2. Intents a bot must never claim to have settled.
        if intent in NEVER_BOT_RESOLVABLE:
            return ResolutionOutcome(
                Verdict.stuck, NEVER_BOT_RESOLVABLE[intent],
                f"{intent.value} is never bot-resolvable",
            )

        # 3. We could not state a fact safely — do not paper over it.
        if claim_unverifiable:
            return ResolutionOutcome(
                Verdict.stuck, HandoffReason.verification_failed,
                "a factual claim could not be verified",
            )

        # 4. Explicit frustration.
        if _matches(customer_text, _STUCK):
            return ResolutionOutcome(Verdict.stuck, HandoffReason.unresolved, "customer signalled stuck")

        # 5. Explicit confirmation — the only path to `resolved`.
        if _matches(customer_text, _CONFIRMED):
            return ResolutionOutcome(Verdict.resolved, evidence="customer confirmed")

        # 6. Guardrail. Hitting the ceiling ESCALATES; it never silently closes.
        #    A customer dropped mid-conversation is worse than one handed to a
        #    person (§3.1's "repeated failures => escalate").
        if turns_used >= max_turns:
            return ResolutionOutcome(
                Verdict.stuck, HandoffReason.max_turns, f"max turns ({max_turns}) reached"
            )

        return ResolutionOutcome(Verdict.unresolved)


# --------------------------------------------------------------------------- #
# AJ — booking attribution
# --------------------------------------------------------------------------- #
@dataclass
class BookingSignal:
    """A completed booking, as it would arrive from the M01/M02 stream.

    NOTE: M01 (A3) does not exist yet, so no real producer emits this. The shape
    is what M12 expects; POA/12 §11 records that AJ is unverified end to end.
    """
    customer_id: str
    booking_id: str
    completed_at: datetime


@dataclass
class AttributionWindow:
    """§3.4 — a booking counts if it lands within `window` AFTER resolution.

    Two rules that are easy to get wrong:

    * a booking **before** the conversation is not attributable — the customer
      was already going to book, and claiming it would inflate the conversion
      metric the whole feature is judged on;
    * the boundary is **inclusive** — a booking exactly at the window edge
      counts. Chosen deliberately so the rule is stable rather than
      floating-point dependent, and asserted at both edges.
    """
    window: timedelta = timedelta(hours=24)

    def attributes(self, resolved_at: datetime, booking_at: datetime) -> bool:
        if booking_at < resolved_at:
            return False
        return (booking_at - resolved_at) <= self.window
