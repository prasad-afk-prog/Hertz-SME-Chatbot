"""Conversation Orchestrator (M08) — POA/08, flow nodes Q, T, U, V.

    fire (Q) -> assemble (T) -> personalise (U) -> prompt (V)
             -> M09 generate -> M10 verify -> M11 deliver -> persist -> confirm M05

M08 is the state owner. It decides nothing about *how* to generate, verify or
deliver — those are M09, M10 and M11 — and everything about *what context* goes
in and *what happens when a stage fails*.

**The rule the whole module serves is POA/08 §6:** on any downstream failure the
customer still receives a safe fallback — never nothing, never an error. There
are five distinct failure points and each has its own correct behaviour:

| Stage fails | What happens |
|---|---|
| Context assembly | bundle marked `degraded`; generation continues with a general message |
| Budget exhausted | fallback, no partial send |
| M09 (provider down / low confidence) | M09 already returns its own templated fallback |
| **M10 returns `blocked`** | fallback — M10 refused a draft quoting an unverifiable price, and silence is not the answer |
| M11 delivery fails | conversation marked failed, and the M05 reservation is **rolled back** |

That fourth row is the easy one to miss: M10's `blocked=True` is a *successful*
call that yields no deliverable text, so a naive orchestrator would send nothing.

**Reservation rollback matters more than it looks.** A fired trigger consumes one
of the customer's capped engagements. If delivery then fails and we do not hand
it back, the customer silently loses an engagement they never received, and the
cap tightens over time.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from generator.models import Event, MessageKind, TriggerConfig
from services.common.resilience import Clock
from services.conversation.claim_verification import ClaimVerificationService
from services.conversation.delivery import DeepLinkAction, DeliveryService, DeliveryStatus
from services.conversation.llm import FallbackReason, LLMService

from .context import ContextAssembler, ContextBundle
from .personalisation import Personalisation, PersonalisationResolver
from .prompts import BuiltPrompt, PromptBuilder
from .state import (
    Conversation,
    ConversationStatus,
    ConversationStore,
    Deadline,
    InMemoryConversationStore,
    InMemoryReservationClient,
    ReservationClient,
    Turn,
    TurnRole,
    new_conversation_id,
)


class Outcome(str, Enum):
    delivered = "delivered"
    delivered_fallback = "delivered_fallback"
    queued = "queued"
    suppressed = "suppressed"
    failed = "failed"


class FailedStage(str, Enum):
    none = "none"
    assembly = "assembly"
    budget = "budget"
    generation = "generation"
    verification = "verification"
    delivery = "delivery"


@dataclass
class FireResult:
    """Everything an audit trail (M14) needs about one fired trigger."""
    outcome: Outcome
    conversation: Conversation | None = None
    delivered_text: str = ""
    message_kind: MessageKind | None = None
    failed_stage: FailedStage = FailedStage.none
    used_fallback: bool = False
    fallback_reason: FallbackReason = FallbackReason.none
    personalisation: Personalisation | None = None
    prompt: BuiltPrompt | None = None
    bundle: ContextBundle | None = None
    reservation_confirmed: bool = False
    reservation_rolled_back: bool = False
    elapsed_s: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def customer_got_something(self) -> bool:
        """POA/08 §6's headline criterion."""
        return self.outcome in (Outcome.delivered, Outcome.delivered_fallback, Outcome.queued)


class ConversationOrchestrator:
    def __init__(
        self,
        assembler: ContextAssembler,
        llm: LLMService,
        verifier: ClaimVerificationService,
        delivery: DeliveryService,
        *,
        store: ConversationStore | None = None,
        reservations: ReservationClient | None = None,
        resolver: PersonalisationResolver | None = None,
        prompts: PromptBuilder | None = None,
        budget_s: float = 5.0,
        clock: Clock = time.monotonic,
    ) -> None:
        self.assembler = assembler
        self.llm = llm
        self.verifier = verifier
        self.delivery = delivery
        self.store = store or InMemoryConversationStore()
        self.reservations = reservations or InMemoryReservationClient()
        self.resolver = resolver or PersonalisationResolver()
        self.prompts = prompts or PromptBuilder()
        self.budget_s = budget_s
        self.clock = clock

    # ---- Q: a trigger fired --------------------------------------------- #
    def on_fire(
        self,
        trigger: TriggerConfig,
        customer_id: str,
        *,
        signals: list[Event] | None = None,
        reservation_id: str | None = None,
        actions: list[DeepLinkAction] | None = None,
    ) -> FireResult:
        deadline = Deadline(self.budget_s, clock=self.clock)
        deadline.start()

        # T — assemble. Never fatal: a degraded bundle still yields a safe message.
        bundle = self.assembler.assemble(trigger, customer_id, signals)

        # U — personalise.
        personalisation = self.resolver.resolve(
            bundle.customer.get("customer_type"),
            bundle.customer.get("region"),
            bundle.customer.get("language"),
            bundle.customer.get("segment"),
        )
        bundle.personalisation = personalisation.as_dict()

        # V — build the prompt.
        prompt = self.prompts.build(
            bundle, personalisation.tone, personalisation.locale, personalisation.formality
        )

        result = FireResult(
            outcome=Outcome.failed,
            personalisation=personalisation,
            prompt=prompt,
            bundle=bundle,
        )
        if bundle.degraded:
            result.notes.append("context degraded: a profile or history lookup failed")
        if personalisation.locale_missing:
            result.notes.append(
                f"unsupported language {bundle.customer.get('language')!r}; served en"
            )

        signal_type = trigger.match.signal_type

        if deadline.expired:
            # Never start work we cannot finish in time.
            text, kind = self._fallback(signal_type, personalisation.locale, bundle)
            result.failed_stage = FailedStage.budget
            result.notes.append("latency budget exhausted before generation")
            return self._deliver_and_finish(
                result, customer_id, trigger, text, kind, actions, reservation_id, deadline,
                used_fallback=True,
            )

        # --- M09: generate ------------------------------------------------ #
        generation = self.llm.generate(
            prompt.text,
            signal=signal_type,
            locale=personalisation.locale,
            context=self._template_context(bundle),
        )
        result.fallback_reason = generation.record.reason
        text, kind = generation.text, generation.message_kind
        used_fallback = generation.message_kind is MessageKind.fallback
        if used_fallback:
            result.failed_stage = FailedStage.generation

        # --- M10: verify any factual claims ------------------------------- #
        if generation.claims:
            verified = self.verifier.verify_response(text, generation.claims)
            if verified.blocked or not verified.delivered_text:
                # M10 refused: the draft quoted something it could not verify.
                # Silence is not the answer — fall back.
                text, kind = self._fallback(signal_type, personalisation.locale, bundle)
                used_fallback = True
                result.failed_stage = FailedStage.verification
                result.notes.append("verification blocked the draft; fell back")
            else:
                text, kind = verified.delivered_text, verified.message_kind
                if verified.introduced_claims:      # should be impossible; assert loudly
                    result.notes.append(
                        f"UNVERIFIED CLAIM SURVIVED: {verified.introduced_claims}"
                    )

        if deadline.expired and not used_fallback:
            text, kind = self._fallback(signal_type, personalisation.locale, bundle)
            used_fallback = True
            result.failed_stage = FailedStage.budget
            result.notes.append("latency budget exhausted before delivery")

        return self._deliver_and_finish(
            result, customer_id, trigger, text, kind, actions, reservation_id, deadline,
            used_fallback=used_fallback,
        )

    # ---- M11 + persist + M05 -------------------------------------------- #
    def _deliver_and_finish(
        self, result, customer_id, trigger, text, kind, actions, reservation_id, deadline,
        *, used_fallback: bool,
    ) -> FireResult:
        conversation = Conversation(
            conversation_id=new_conversation_id(),
            customer_id=customer_id,
            trigger_id=trigger.trigger_id,
            locale=result.personalisation.locale if result.personalisation else "en",
            created_at=self.clock(),
        )

        receipt = self.delivery.deliver(
            conversation.conversation_id, text, actions, proactive=True
        )
        conversation.thread_id = receipt.thread_id

        if receipt.status is DeliveryStatus.delivered:
            outcome = Outcome.delivered_fallback if used_fallback else Outcome.delivered
        elif receipt.status is DeliveryStatus.queued:
            outcome = Outcome.queued
        elif receipt.status is DeliveryStatus.suppressed:
            outcome = Outcome.suppressed
        else:
            outcome = Outcome.failed
            result.failed_stage = FailedStage.delivery

        if outcome is Outcome.failed:
            conversation.status = ConversationStatus.failed
        else:
            conversation.add_turn(Turn(
                role=TurnRole.bot,
                text=text,
                at=self.clock(),
                message_kind=kind.value if kind else None,
                prompt_version=result.prompt.version if result.prompt else None,
                used_fallback=used_fallback,
            ))

        # State is persisted whatever happened — a failed engagement is still a
        # fact M14 needs, and M12 must not resume a conversation that failed.
        self.store.create(conversation)

        # M05: hand the engagement back if the customer never received anything.
        if reservation_id:
            if outcome in (Outcome.delivered, Outcome.delivered_fallback, Outcome.queued):
                self.reservations.confirm(reservation_id)
                result.reservation_confirmed = True
            else:
                self.reservations.rollback(reservation_id, reason=outcome.value)
                result.reservation_rolled_back = True

        result.outcome = outcome
        result.conversation = conversation
        result.delivered_text = text if outcome is not Outcome.failed else ""
        result.message_kind = kind
        result.used_fallback = used_fallback
        result.elapsed_s = deadline.elapsed_s
        return result

    # ---- helpers --------------------------------------------------------- #
    def _fallback(self, signal_type, locale, bundle) -> tuple[str, MessageKind]:
        rendered = self.llm.catalogue.render(signal_type, locale, self._template_context(bundle))
        return rendered.text, MessageKind.fallback

    @staticmethod
    def _template_context(bundle: ContextBundle) -> dict[str, str]:
        """Slots the fallback templates may fill. Allow-listed like the bundle —
        a template slot is another path to the customer."""
        context: dict[str, str] = {}
        latest = bundle.recent_signals[-1] if bundle.recent_signals else {}
        pickup = latest.get("pickup") or (
            bundle.booking_history[0].get("pickup") if bundle.booking_history else None
        )
        if pickup:
            context["route"] = pickup
        vehicle = latest.get("vehicle_class") or (
            bundle.booking_history[0].get("vehicle_class") if bundle.booking_history else None
        )
        if vehicle:
            context["vehicle"] = vehicle
        return context
