"""Customer Response & Multi-turn Conversation Manager (M12) — POA/12, AE–AK.

What happens after the customer sees the proactive message:

    DELIVERED ─(no reply in window)─► NO_ENGAGEMENT (AF)
    DELIVERED ─(reply)─► ACTIVE ──(turn: M09 → M10 → M11)── ACTIVE
    ACTIVE ─resolved──► DEEP_LINK (AI) ─(booking in window)─► CONVERTED (AJ)
    ACTIVE ─stuck─────► HANDOFF (AK)

M12 owns the loop; it owns none of the pieces. Each bot turn reuses M09
(generate), M10 (verify) and M11 (deliver) exactly as M08 does — POA/12 §3.2 is
explicit about reusing that orchestration rather than growing a second one — and
the conversation state belongs to M08 throughout.

**The terminal decision delegates to `reference.terminal_state`.** That function
is the executable spec and `test_golden_scenarios.py` asserts against it via the
AE/AH branches. The resolution detector layers around it, same as M09's
heuristics layer around `decide_llm`.

**Two behaviours worth stating outright:**

*Hitting the max-turns guardrail raises a handoff; it never silently closes.* A
customer dropped mid-conversation is worse than one handed to a person, and §3.1
asks for exactly that escalation.

*A handoff is raised, never handled inline.* M12 emits a `HandoffEvent` to a
sink and stops. Routing is M04's job, then M07's.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from generator.models import Intent, MessageKind, TerminalState
from generator.reference import terminal_state
from services.common.resilience import Clock
from services.conversation.claim_verification import ClaimVerificationService
from services.conversation.delivery import (
    ActionKind,
    DeepLinkAction,
    DeliveryService,
    DeliveryStatus,
)
from services.conversation.llm import LLMService
from services.conversation.orchestrator.state import (
    Conversation,
    ConversationStatus,
    ConversationStore,
    Turn,
    TurnRole,
)

from .handoff import (
    HandoffEvent,
    HandoffReason,
    HandoffSink,
    InMemoryHandoffSink,
    TranscriptTurn,
)
from .resolution import (
    AttributionWindow,
    BookingSignal,
    ResolutionDetector,
    Verdict,
)

_TZ = timezone.utc


@dataclass
class TurnResult:
    """One customer turn and the bot's reply to it."""
    bot_text: str
    message_kind: MessageKind
    verdict: Verdict
    used_fallback: bool = False
    claim_stripped: bool = False


@dataclass
class ConversationOutcome:
    """The terminal record for one conversation (§2: persist every outcome)."""
    conversation_id: str
    customer_id: str
    trigger_id: str
    # None means "resolved, awaiting attribution" — see the note below.
    terminal: TerminalState | None
    status: ConversationStatus
    turns_used: int = 0
    handoff: HandoffEvent | None = None
    deep_link: DeepLinkAction | None = None
    booking_id: str | None = None
    resolved_at: datetime | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def converted(self) -> bool:
        return self.terminal is TerminalState.converted


class ResponseManager:
    def __init__(
        self,
        store: ConversationStore,
        llm: LLMService,
        verifier: ClaimVerificationService,
        delivery: DeliveryService,
        *,
        handoff_sink: HandoffSink | None = None,
        detector: ResolutionDetector | None = None,
        attribution: AttributionWindow | None = None,
        no_response_after: timedelta = timedelta(hours=2),
        max_turns: int = 4,
        clock: Clock = time.monotonic,
        now: callable = lambda: datetime.now(_TZ),
    ) -> None:
        self.store = store
        self.llm = llm
        self.verifier = verifier
        self.delivery = delivery
        self.handoff_sink = handoff_sink or InMemoryHandoffSink()
        self.detector = detector or ResolutionDetector()
        self.attribution = attribution or AttributionWindow()
        self.no_response_after = no_response_after
        self.max_turns = max_turns
        self.clock = clock
        self.now = now
        self.outcomes: list[ConversationOutcome] = []

    # ---- AE / AF: did they respond? ------------------------------------- #
    def check_no_response(self, conversation_id: str, at: datetime) -> ConversationOutcome | None:
        """§3.1's scheduled check. Celery drives this in production (A7/M15);
        the timing is a parameter here so the window is asserted, not slept for.
        """
        conversation = self.store.get(conversation_id)
        if conversation is None or conversation.status is not ConversationStatus.open:
            return None

        delivered_at = self._delivered_at(conversation)
        if delivered_at is None or (at - delivered_at) < self.no_response_after:
            return None

        conversation.status = ConversationStatus.no_engagement
        self.store.save(conversation)
        return self._record(
            conversation,
            terminal=terminal_state(responded=False, resolves=None),
            status=ConversationStatus.no_engagement,
            notes=[f"no reply within {self.no_response_after}"],
        )

    # ---- AG: one customer turn ------------------------------------------ #
    def on_customer_reply(
        self,
        conversation_id: str,
        text: str,
        *,
        intent: Intent | None = None,
        at: datetime | None = None,
    ) -> TurnResult | ConversationOutcome | None:
        """Run one turn. Returns a `TurnResult` while the loop continues, or a
        `ConversationOutcome` when this reply ends the conversation."""
        conversation = self.store.get(conversation_id)
        if conversation is None:
            return None
        if conversation.status in _TERMINAL:
            return None                       # already finished; ignore late replies

        at = at or self.now()
        conversation.status = ConversationStatus.active
        conversation.add_turn(Turn(role=TurnRole.customer, text=text, at=at.timestamp()))

        turns_used = self._customer_turns(conversation)

        # M09 -> M10 -> M11, exactly as M08 does it.
        generation = self.llm.generate(
            self._prompt(conversation, text),
            locale=conversation.locale,
        )
        bot_text, kind = generation.text, generation.message_kind
        used_fallback = kind is MessageKind.fallback
        claim_stripped = False

        if generation.claims:
            verified = self.verifier.verify_response(bot_text, generation.claims)
            if verified.blocked or not verified.delivered_text:
                claim_stripped = True
                bot_text, kind = generation.fallback.text if generation.fallback else bot_text, MessageKind.fallback
                used_fallback = True
            else:
                bot_text, kind = verified.delivered_text, verified.message_kind
                claim_stripped = kind is MessageKind.stripped

        verdict = self.detector.assess(
            text, intent=intent, turns_used=turns_used,
            max_turns=self.max_turns, claim_unverifiable=claim_stripped,
        )

        if verdict.verdict is Verdict.stuck:
            return self._raise_handoff(conversation, verdict.reason or HandoffReason.unresolved,
                                       at, notes=[verdict.evidence] if verdict.evidence else [])

        receipt = self.delivery.deliver(conversation.conversation_id, bot_text, proactive=False)
        if receipt.status is not DeliveryStatus.delivered:
            # We cannot continue a conversation we cannot speak into.
            return self._raise_handoff(
                conversation, HandoffReason.unresolved, at,
                notes=[f"delivery failed: {receipt.reason}"],
            )

        conversation.add_turn(Turn(
            role=TurnRole.bot, text=bot_text, at=at.timestamp(),
            message_kind=kind.value if kind else None, used_fallback=used_fallback,
        ))
        self.store.save(conversation)

        if verdict.verdict is Verdict.resolved:
            return self._resolve(conversation, at)

        return TurnResult(bot_text, kind, verdict.verdict, used_fallback, claim_stripped)

    # ---- AI: resolved -> deep link -------------------------------------- #
    def _resolve(self, conversation: Conversation, at: datetime) -> ConversationOutcome:
        action = DeepLinkAction(
            kind=ActionKind.resume_booking,
            label="Pick up where you left off",
            target=f"conversation:{conversation.conversation_id}#resume",
            params={"trigger_id": conversation.trigger_id},
        )
        self.delivery.deliver(
            conversation.conversation_id,
            "Here's where you left off — shall we finish it?",
            [action],
            proactive=False,
        )
        conversation.status = ConversationStatus.deep_link
        self.store.save(conversation)

        # NOT terminal yet, and deliberately NOT `terminal_state(True, True)`.
        #
        # FINDING (POA/12 §11): `reference.terminal_state` has three outcomes —
        # no_engagement / converted / handed_off — but this flow has four. A
        # customer the bot helped, who was shown a deep link and has not (yet)
        # booked, is none of those. Calling terminal_state(True, True) here
        # returns `converted` and would count a booking that never happened,
        # inflating the exact metric M14 reports and this feature is judged on.
        #
        # So resolution leaves `terminal` as None until AJ attributes a real
        # booking. The gap is surfaced rather than forked — the shared spec is
        # the right place to fix it, once its owner decides what the fourth
        # outcome is called.
        return self._record(
            conversation,
            terminal=None,
            status=ConversationStatus.deep_link,
            deep_link=action,
            resolved_at=at,
        )

    # ---- AJ: booking attribution ---------------------------------------- #
    def attribute_booking(self, signal: BookingSignal) -> ConversationOutcome | None:
        """§3.4. A booking is attributed to the most recent resolved conversation
        for that customer, if it lands inside the window."""
        candidates = [
            o for o in self.outcomes
            if o.customer_id == signal.customer_id
            and o.resolved_at is not None
            and o.booking_id is None
            and self.attribution.attributes(o.resolved_at, signal.completed_at)
        ]
        if not candidates:
            return None

        outcome = max(candidates, key=lambda o: o.resolved_at)   # most recent wins
        outcome.booking_id = signal.booking_id
        outcome.terminal = TerminalState.converted
        outcome.status = ConversationStatus.converted

        conversation = self.store.get(outcome.conversation_id)
        if conversation is not None:
            conversation.status = ConversationStatus.converted
            self.store.save(conversation)
        return outcome

    # ---- AK: handoff ----------------------------------------------------- #
    def _raise_handoff(
        self, conversation: Conversation, reason: HandoffReason, at: datetime,
        notes: list[str] | None = None,
    ) -> ConversationOutcome:
        event = HandoffEvent(
            conversation_id=conversation.conversation_id,
            customer_id=conversation.customer_id,
            trigger_id=conversation.trigger_id,
            reason=reason,
            locale=conversation.locale,
            raised_at=at,
            transcript=[
                TranscriptTurn(
                    role=t.role.value, text=t.text,
                    at=datetime.fromtimestamp(t.at, _TZ),
                )
                for t in conversation.turns
            ],
            thread_id=conversation.thread_id,
            notes=list(notes or []),
        )
        self.handoff_sink.raise_handoff(event)

        conversation.status = ConversationStatus.handed_off
        self.store.save(conversation)
        return self._record(
            conversation,
            terminal=terminal_state(responded=True, resolves=False),
            status=ConversationStatus.handed_off,
            handoff=event,
            notes=list(notes or []),
        )

    # ---- helpers --------------------------------------------------------- #
    def _record(self, conversation, *, terminal, status, **kw) -> ConversationOutcome:
        outcome = ConversationOutcome(
            conversation_id=conversation.conversation_id,
            customer_id=conversation.customer_id,
            trigger_id=conversation.trigger_id,
            terminal=terminal,
            status=status,
            turns_used=self._customer_turns(conversation),
            **kw,
        )
        self.outcomes.append(outcome)
        return outcome

    @staticmethod
    def _customer_turns(conversation: Conversation) -> int:
        return sum(1 for t in conversation.turns if t.role is TurnRole.customer)

    @staticmethod
    def _delivered_at(conversation: Conversation) -> datetime | None:
        for turn in conversation.turns:
            if turn.role is TurnRole.bot:
                return datetime.fromtimestamp(turn.at, _TZ)
        return None

    @staticmethod
    def _prompt(conversation: Conversation, latest: str) -> str:
        """Context retention (§2). The whole transcript goes back each turn —
        the API is stateless, so 'retained context' means resending it."""
        history = "\n".join(f"{t.role.value}: {t.text}" for t in conversation.turns)
        return f"{history}\ncustomer: {latest}\n\nReply to the customer's latest message."


_TERMINAL = {
    ConversationStatus.no_engagement,
    ConversationStatus.handed_off,
    ConversationStatus.converted,
    ConversationStatus.failed,
    ConversationStatus.closed,
}
