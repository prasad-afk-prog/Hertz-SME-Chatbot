"""A tiny reference pipeline that wires the mocks + reference decisions together,
so golden scenarios can be executed end-to-end before the real services exist.

Order mirrors the flow: M05 cap -> M09 generate/decide -> M10 verify/resolve ->
M11 deliver -> M12 terminal.
"""
from __future__ import annotations

from dataclasses import dataclass

from generator.models import MessageKind, Scenario, TerminalState
from generator.reference import (
    apply_verification,
    decide_llm,
    fallback_message,
    terminal_state,
    would_fire,
)
from generator.world import World
from mocks import BookingAPIMock, HS103Mock, LLMProviderMock, LLMTimeout


@dataclass
class Result:
    fired: bool
    suppressed_reason: str | None
    delivered: str | None
    message_kind: MessageKind | None
    terminal_state: TerminalState | None


def run_scenario(sc: Scenario, world: World) -> Result:
    now = sc.events[-1].occurred_at

    # M05 — frequency cap / precedence gate
    if not would_fire(sc.prior_engagements, now, sc.trigger.frequency_cap):
        return Result(False, "frequency_cap", None, None, None)

    # M09 — generate + availability/confidence decision
    llm = LLMProviderMock(sc.llm_response, timeout=sc.llm_timeout)
    try:
        result = llm.generate()
    except LLMTimeout:
        result = None

    if decide_llm(result) == "fallback":
        delivered, kind = fallback_message(sc.customer.language), MessageKind.fallback
    else:
        # M10 — verify every factual claim against the live (world-backed) API
        booking = BookingAPIMock(world, failures=sc.booking_api_failures)
        results = [booking.verify(c) for c in result.claims]
        delivered, kind = apply_verification(result.text, result.claims, results)

    # M11 — deliver
    HS103Mock(replies=sc.replies).deliver(f"conv-{sc.scenario_id}", delivered)

    # M12 — terminal state
    responded = bool(sc.replies)
    return Result(True, None, delivered, kind, terminal_state(responded, sc.resolves))
