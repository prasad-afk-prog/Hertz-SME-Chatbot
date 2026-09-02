"""Conversation state + the M05 reservation contract (M08 §3.4, §5.1, §5.6).

M08 owns conversation state — POA/08 §1 is explicit that it "owns the
conversation session/state that M12 continues". So this is the handoff point to
M12, and the shape matters more than the storage.

**The reservation contract is a CROSS-TRACK contract with Prasad's M05, and it
is unagreed.** §2 requires M08 to "confirm/rollback the M05 engagement
reservation based on successful delivery": a fired trigger consumes one of the
customer's capped engagements, and if delivery then fails, that engagement must
be handed back or the cap silently tightens. `ReservationClient` is a protocol
with an in-memory implementation, and the real shape needs agreeing with Prasad —
recorded in POA/08 §11 and added to POA/18 §5's handshake list.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from services.common.resilience import Clock


class ConversationStatus(str, Enum):
    """The M08 -> M12 lifecycle (POA/12 §3.1).

    M08 writes `open` (delivered, awaiting the customer) or `failed`. Everything
    after that is M12's: it moves `open` through the response loop to one of the
    three terminal states. Additive only — `open` and `failed` keep the meanings
    M08 and its tests already rely on.
    """
    open = "open"                  # delivered, awaiting the customer (-> M12)
    delivered_no_reply = "delivered_no_reply"
    failed = "failed"              # nothing reached the customer
    closed = "closed"
    # ---- M12 (POA/12 §3.1) ------------------------------------------- #
    active = "active"              # customer replied; multi-turn loop running
    deep_link = "deep_link"        # AI — resolved, deep link surfaced
    converted = "converted"        # AJ — booking attributed to the intervention
    no_engagement = "no_engagement"  # AF — no reply inside the window
    handed_off = "handed_off"      # AK — handoff event raised


class TurnRole(str, Enum):
    bot = "bot"
    customer = "customer"


@dataclass
class Turn:
    role: TurnRole
    text: str
    at: float
    message_kind: str | None = None
    prompt_version: str | None = None
    used_fallback: bool = False


@dataclass
class Conversation:
    """The state M12 picks up. Deliberately transport-agnostic."""
    conversation_id: str
    customer_id: str
    trigger_id: str
    locale: str
    status: ConversationStatus = ConversationStatus.open
    turns: list[Turn] = field(default_factory=list)
    thread_id: str | None = None
    created_at: float = 0.0

    def add_turn(self, turn: Turn) -> None:
        self.turns.append(turn)

    @property
    def last_bot_text(self) -> str | None:
        for turn in reversed(self.turns):
            if turn.role is TurnRole.bot:
                return turn.text
        return None


@runtime_checkable
class ConversationStore(Protocol):
    def create(self, conversation: Conversation) -> None: ...
    def get(self, conversation_id: str) -> Conversation | None: ...
    def save(self, conversation: Conversation) -> None: ...
    def for_customer(self, customer_id: str) -> list[Conversation]: ...


class InMemoryConversationStore:
    """Phase-1 store. Postgres (§4) needs M15, which is Prasad's A1."""

    def __init__(self) -> None:
        self._by_id: dict[str, Conversation] = {}

    def create(self, conversation: Conversation) -> None:
        if conversation.conversation_id in self._by_id:
            raise ValueError(f"conversation {conversation.conversation_id} already exists")
        self._by_id[conversation.conversation_id] = conversation

    def get(self, conversation_id: str) -> Conversation | None:
        return self._by_id.get(conversation_id)

    def save(self, conversation: Conversation) -> None:
        self._by_id[conversation.conversation_id] = conversation

    def for_customer(self, customer_id: str) -> list[Conversation]:
        return [c for c in self._by_id.values() if c.customer_id == customer_id]

    def __len__(self) -> int:
        return len(self._by_id)


def new_conversation_id() -> str:
    return f"conv-{uuid.uuid4()}"


# --------------------------------------------------------------------------- #
# M05 reservation — CROSS-TRACK, contract unagreed (POA/18 §5)
# --------------------------------------------------------------------------- #
@runtime_checkable
class ReservationClient(Protocol):
    """A fired trigger reserves one of the customer's capped engagements.

    Confirm on successful delivery; roll back otherwise, or the customer silently
    loses an engagement they never received.
    """

    def confirm(self, reservation_id: str) -> None: ...
    def rollback(self, reservation_id: str, reason: str) -> None: ...


@dataclass
class InMemoryReservationClient:
    """Phase-1 stand-in until the real M05 contract is agreed with Prasad."""
    confirmed: list[str] = field(default_factory=list)
    rolled_back: list[tuple[str, str]] = field(default_factory=list)

    def confirm(self, reservation_id: str) -> None:
        self.confirmed.append(reservation_id)

    def rollback(self, reservation_id: str, reason: str) -> None:
        self.rolled_back.append((reservation_id, reason))


# --------------------------------------------------------------------------- #
# Latency budget (§5.7)
# --------------------------------------------------------------------------- #
@dataclass
class Deadline:
    """One budget the whole chain draws down against.

    Generation, verification and delivery each cost time, and the customer is
    waiting. Exhausting the budget must produce a *fallback*, never a partial
    send — a half-delivered message is worse than a generic complete one.
    """
    budget_s: float
    clock: Clock = time.monotonic
    started_at: float = field(default=0.0, init=False)

    def start(self) -> None:
        self.started_at = self.clock()

    @property
    def elapsed_s(self) -> float:
        return self.clock() - self.started_at

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.budget_s - self.elapsed_s)

    @property
    def expired(self) -> bool:
        return self.remaining_s <= 0.0
