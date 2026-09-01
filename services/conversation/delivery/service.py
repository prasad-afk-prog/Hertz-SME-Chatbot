"""Chatbot UI Integration & Delivery (M11) — POA/11, flow node AD.

Delivers the finalised message into the customer's existing HS-103 session and
captures their replies back for M12. This is a transport/adapter layer: it owns
none of the message content (M08/M09/M10) and none of the response logic (M12).

**The integration surface is unknown — POA/11 §10.1 is open** (REST push?
websocket? widget SDK? does HS-103 even support proactive injection?). That is
the module's central risk, and §8's mitigation is "adapter abstraction". So
`HS103Adapter` is a protocol with the existing mock behind it, and everything
this module actually decides — presence, anti-nag, correlation, receipts, retry
— sits *above* the transport and does not change when the real surface lands.

Two rules that are easy to get wrong:

**Anti-nag is a UI-level rule, not a second frequency cap.** M05 already enforces
how often a customer may be *engaged*. Re-implementing that here would double-
count and silently halve the configured cap. What this module adds is narrower:
don't stack a second proactive message on a conversation that already has one
undelivered or unacknowledged.

**Inbound handling must be idempotent.** §8 names reply-correlation errors as a
risk; a webhook that delivers twice is normal, and processing a reply twice
would put a duplicate turn into M12.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from services.common.resilience import Clock


class DeliveryStatus(str, Enum):
    delivered = "delivered"
    queued = "queued"            # widget closed — badge/next-open (§3.2)
    suppressed = "suppressed"    # anti-nag (§3.2)
    failed = "failed"


class ActionKind(str, Enum):
    """Deep-link affordances HS-103 renders as buttons (§3.3)."""
    resume_booking = "resume_booking"
    view_rates = "view_rates"
    change_vehicle = "change_vehicle"
    contact_agent = "contact_agent"


@dataclass
class DeepLinkAction:
    """A structured action, not a URL baked into prose.

    Structured because §3.3 requires HS-103 to render it as an affordance, and
    because a URL inside message text would bypass M10 — a link carrying a price
    in its query string is still a claim.
    """
    kind: ActionKind
    label: str
    target: str                                   # e.g. "booking:HFB-000123#payment"
    params: dict[str, str] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "label": self.label,
            "target": self.target,
            "params": dict(self.params),
        }


@dataclass
class DeliveryReceipt:
    """Feeds the M14 engagement metric (§3.4)."""
    conversation_id: str
    thread_id: str | None
    status: DeliveryStatus
    delivery_ref: str | None = None
    attempts: int = 0
    reason: str | None = None
    read_at: float | None = None

    @property
    def reached_customer(self) -> bool:
        return self.status is DeliveryStatus.delivered


@dataclass
class InboundMessage:
    """A customer reply on its way to M12."""
    message_id: str
    conversation_id: str
    thread_id: str
    text: str
    received_at: float


# --------------------------------------------------------------------------- #
# The transport seam (§3.1, §10.1 open)
# --------------------------------------------------------------------------- #
@runtime_checkable
class HS103Adapter(Protocol):
    def deliver(self, conversation_id: str, text: str) -> str: ...


class MockHS103Adapter:
    """Puts `mocks.hs103.HS103Mock` behind the adapter protocol.

    `present` models whether the customer's widget is actually open, which the
    mock has no concept of — presence is a §3.2 requirement, so it is simulated
    here rather than assumed true.
    """

    def __init__(self, mock: Any, present: bool = True, supports_actions: bool = True) -> None:
        self._mock = mock
        self.present = present
        self.supports_actions = supports_actions
        self.delivered_actions: list[list[dict[str, Any]]] = []

    def deliver(self, conversation_id: str, text: str, actions: list[DeepLinkAction] | None = None) -> str:
        ref = self._mock.deliver(conversation_id, text)
        if actions and self.supports_actions:
            self.delivered_actions.append([a.to_payload() for a in actions])
        return ref

    def inbound(self) -> list[str]:
        return self._mock.inbound()


# --------------------------------------------------------------------------- #
# Correlation store (§3.4, §5.4)
# --------------------------------------------------------------------------- #
class CorrelationStore:
    """conversation_id <-> hs103_thread_id, both directions.

    Both directions matter: outbound needs conversation -> thread, and an
    inbound webhook arrives with only the thread id.
    """

    def __init__(self) -> None:
        self._to_thread: dict[str, str] = {}
        self._to_conversation: dict[str, str] = {}

    def bind(self, conversation_id: str, thread_id: str) -> None:
        existing = self._to_thread.get(conversation_id)
        if existing and existing != thread_id:
            raise ValueError(
                f"conversation {conversation_id} is already bound to thread {existing}; "
                "rebinding would misroute replies"
            )
        self._to_thread[conversation_id] = thread_id
        self._to_conversation[thread_id] = conversation_id

    def thread_for(self, conversation_id: str) -> str | None:
        return self._to_thread.get(conversation_id)

    def conversation_for(self, thread_id: str) -> str | None:
        return self._to_conversation.get(thread_id)

    def __len__(self) -> int:
        return len(self._to_thread)


# --------------------------------------------------------------------------- #
# Delivery service
# --------------------------------------------------------------------------- #
class DeliveryService:
    def __init__(
        self,
        adapter: HS103Adapter,
        correlation: CorrelationStore | None = None,
        *,
        max_attempts: int = 3,
        clock: Clock = time.monotonic,
    ) -> None:
        self.adapter = adapter
        self.correlation = correlation or CorrelationStore()
        self.max_attempts = max_attempts
        self.clock = clock
        self.receipts: list[DeliveryReceipt] = []
        self._pending: set[str] = set()          # conversations awaiting acknowledgement
        self._seen_inbound: set[str] = set()      # idempotency for webhooks

    # ---- AD: deliver ---------------------------------------------------- #
    def deliver(
        self,
        conversation_id: str,
        text: str,
        actions: list[DeepLinkAction] | None = None,
        *,
        thread_id: str | None = None,
        proactive: bool = True,
    ) -> DeliveryReceipt:
        if not text:
            # M09/M10 refused to produce a safe message. Nothing to deliver, and
            # an empty bubble is worse than silence.
            return self._record(conversation_id, thread_id, DeliveryStatus.failed,
                                reason="empty message")

        # Anti-nag (§3.2): narrower than M05's cap — see the module docstring.
        if proactive and conversation_id in self._pending:
            return self._record(conversation_id, thread_id, DeliveryStatus.suppressed,
                                reason="a proactive message on this conversation is unacknowledged")

        present = getattr(self.adapter, "present", True)
        if proactive and not present:
            # §3.2: widget closed -> notification affordance, not an injection.
            self._pending.add(conversation_id)
            return self._record(conversation_id, thread_id, DeliveryStatus.queued,
                                reason="widget closed — queued for next open")

        last_error: str | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                ref = self._call_adapter(conversation_id, text, actions)
            except Exception as exc:                  # transport failures are expected
                last_error = str(exc)
                continue

            # A delivery ref identifies one MESSAGE; a thread identifies the
            # CONVERSATION. Conflating them would try to rebind the conversation
            # on every send and misroute replies, so an existing binding wins
            # and the ref is only used to seed the very first one.
            resolved_thread = (
                thread_id
                or self.correlation.thread_for(conversation_id)
                or ref
            )
            self.correlation.bind(conversation_id, resolved_thread)
            self._pending.add(conversation_id)
            return self._record(conversation_id, resolved_thread, DeliveryStatus.delivered,
                                delivery_ref=ref, attempts=attempt)

        return self._record(conversation_id, thread_id, DeliveryStatus.failed,
                            attempts=self.max_attempts, reason=last_error)

    def _call_adapter(self, conversation_id: str, text: str, actions) -> str:
        supports_actions = getattr(self.adapter, "supports_actions", False)
        if actions and supports_actions:
            return self.adapter.deliver(conversation_id, text, actions)   # type: ignore[call-arg]
        return self.adapter.deliver(conversation_id, text)

    def _record(self, conversation_id, thread_id, status, **kw) -> DeliveryReceipt:
        receipt = DeliveryReceipt(
            conversation_id=conversation_id, thread_id=thread_id, status=status, **kw
        )
        self.receipts.append(receipt)
        return receipt

    # ---- receipts (§3.4 -> M14) ----------------------------------------- #
    def mark_read(self, conversation_id: str) -> bool:
        for receipt in reversed(self.receipts):
            if receipt.conversation_id == conversation_id and receipt.reached_customer:
                receipt.read_at = self.clock()
                return True
        return False

    @property
    def engagement(self) -> dict[str, int]:
        """The delivery-side engagement signal M14 consumes."""
        return {
            "delivered": sum(1 for r in self.receipts if r.status is DeliveryStatus.delivered),
            "queued": sum(1 for r in self.receipts if r.status is DeliveryStatus.queued),
            "suppressed": sum(1 for r in self.receipts if r.status is DeliveryStatus.suppressed),
            "failed": sum(1 for r in self.receipts if r.status is DeliveryStatus.failed),
            "read": sum(1 for r in self.receipts if r.read_at is not None),
        }

    # ---- inbound (§3.1, §5.3 -> M12) ------------------------------------ #
    def on_customer_message(
        self, thread_id: str, text: str, message_id: str | None = None
    ) -> InboundMessage | None:
        """Correlate an inbound reply and hand it to M12.

        Returns None for an unknown thread or a duplicate delivery. Both are
        normal in webhook transports and neither should raise: a 500 back to
        HS-103 would just make it retry the same unusable event.
        """
        conversation_id = self.correlation.conversation_for(thread_id)
        if conversation_id is None:
            return None

        message_id = message_id or str(uuid.uuid4())
        if message_id in self._seen_inbound:
            return None                       # idempotent: §8's correlation risk
        self._seen_inbound.add(message_id)

        # The customer answered, so the conversation is no longer un-acknowledged.
        self._pending.discard(conversation_id)
        return InboundMessage(
            message_id=message_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            text=text,
            received_at=self.clock(),
        )
