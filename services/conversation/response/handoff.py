"""Handoff event raising (M12 node AK) — POA/12 §3.3.

**The routing here is deliberate and easy to get wrong.** §3.3 says the handoff
event goes to **M04**, which recognises the handoff event type, and M04 routes
on to M07. Not straight to M07. M04 is the single place that decides what
happens to a signal, and bypassing it would put a second routing brain in the
system — the thing POA/04 exists to prevent.

Prasad's `triggers/evaluator.py` already documents that seam: *"The handoff
branch (events fed back from M12, POA/04 §10.3) is left as a seam — M07/M12
don't exist yet and its representation is an open question."* This module is the
other half of that seam, and the shape below is a **proposal, not an agreed
contract** — POA/18 §5b item 6.

It mirrors his `sinks.py` on purpose: a Pydantic message, a `Protocol`, and an
in-memory implementation. Same shape as `FireSink`, so wiring it up later is
recognisable rather than novel.

§2 is explicit that a handoff is *raised, never handled inline*: the bot does not
try to route, pick a queue, or promise an agent. It states what happened and
hands over the whole transcript.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class HandoffReason(str, Enum):
    """Why the bot gave up. M07 routes on this, and M14 reports on it."""
    unresolved = "unresolved"            # customer still stuck after the loop
    max_turns = "max_turns"              # guardrail hit — see the note in service.py
    customer_requested = "customer_requested"
    complaint = "complaint"              # never bot-resolvable (see the S3 trees)
    claim_dispute = "claim_dispute"      # billing disputes go to people
    verification_failed = "verification_failed"  # we could not state a fact safely


class TranscriptTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str                            # "bot" | "customer"
    text: str
    at: datetime


class HandoffEvent(BaseModel):
    """M12 -> M04 -> M07. Carries everything an agent needs to not start over.

    The full transcript travels with it: §6 requires context preserved end to
    end, and a handoff that makes the customer repeat themselves is the failure
    mode the whole feature is meant to avoid.
    """
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    customer_id: str
    trigger_id: str
    reason: HandoffReason
    locale: str = "en"
    raised_at: datetime
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    # The signal that started all this — M07 routes on trigger/signal context.
    signal_type: str | None = None
    thread_id: str | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def turn_count(self) -> int:
        return len(self.transcript)


@runtime_checkable
class HandoffSink(Protocol):
    """M12 -> M04. Mirrors Prasad's `FireSink` deliberately."""

    def raise_handoff(self, event: HandoffEvent) -> None: ...


class InMemoryHandoffSink:
    def __init__(self) -> None:
        self.events: list[HandoffEvent] = []

    def raise_handoff(self, event: HandoffEvent) -> None:
        self.events.append(event)
