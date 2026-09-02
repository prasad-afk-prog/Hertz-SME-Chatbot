"""Outcome-event contracts (M14 task 1) — POA/14 §2, §3.1.

Every stage of the pipeline emits one of these. They are the raw, immutable
record that every metric is recomputed from, so §6's *"numbers reconcile with
raw outcome events"* is checkable rather than aspirational.

**The PII boundary, stated precisely.** §8's fourth risk is PII in reports. An
`OutcomeEvent` carries only identifiers, enums and counts — a test asserts its
fields are disjoint from everything `pii.PII_FIELDS` marks. That makes
*aggregates* PII-free by construction.

It does **not** make drill-down PII-free. §3.3's drill-down reaches conversations,
and `HandoffEvent.transcript` is free customer text that may contain anything the
customer typed. So the honest claim is: **aggregates carry no PII; drill-down
reaches PII and needs M15's access control, which does not exist yet.** Recorded
in POA/14 §11.

Events are frozen. The store returns tuples. Same discipline as M13's audit log,
for the same reason: an "immutable" log a caller can edit is not immutable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class OutcomeKind(str, Enum):
    """What happened. One per flow node that produces a reportable fact."""
    # A6 / M05
    fired = "fired"                      # an engagement was approved and reserved
    suppressed = "suppressed"            # Z1 — cap, cooldown or precedence loss
    # M11
    delivered = "delivered"              # reached the customer
    delivery_failed = "delivery_failed"
    read = "read"
    # M12
    responded = "responded"              # AE — the customer replied
    no_engagement = "no_engagement"      # AF
    resolved = "resolved"                # AI — deep link surfaced, booking pending
    converted = "converted"              # AJ — booking attributed
    handed_off = "handed_off"            # AK / AM
    # M09 / M10 quality signals
    fallback_used = "fallback_used"
    claim_corrected = "claim_corrected"
    claim_stripped = "claim_stripped"
    # M06 / A7
    deferred_expired = "deferred_expired"   # Z2


#: Fields an event may carry for segmentation (§2: per trigger, segment, window).
#: Deliberately coarse — customer_type/region/language identify a cohort, not a
#: person. `customer_id` is an opaque synthetic id, classified NOT_PII in S4.
@dataclass(frozen=True)
class Segment:
    customer_type: str | None = None
    region: str | None = None
    language: str | None = None

    def matches(self, other: "Segment") -> bool:
        """True when `self` (a filter) is satisfied by `other` (an event).
        Unset filter fields match anything."""
        for name in ("customer_type", "region", "language"):
            wanted = getattr(self, name)
            if wanted is not None and getattr(other, name) != wanted:
                return False
        return True


@dataclass(frozen=True)
class OutcomeEvent:
    """One immutable reportable fact.

    Frozen on purpose: §3.1 requires raw outcome events stay immutable, and
    every metric is a recount over these.
    """
    kind: OutcomeKind
    at: datetime
    trigger_id: str | None = None
    conversation_id: str | None = None
    customer_id: str | None = None
    segment: Segment = field(default_factory=Segment)
    # Free-form, non-PII detail: a suppression reason, a handoff reason, a
    # fallback reason. Enum values and codes only — never customer text.
    detail: str | None = None
    # Small numeric payload (turns used, delivery attempts). Never text.
    value: float | None = None

    def in_window(self, start: datetime | None, end: datetime | None) -> bool:
        if start is not None and self.at < start:
            return False
        if end is not None and self.at > end:
            return False
        return True


#: Field names an OutcomeEvent may carry. The PII test asserts this set is
#: disjoint from every field pii.PII_FIELDS marks, so adding a PII-bearing
#: field to an analytics event fails the suite.
EVENT_FIELDS: tuple[str, ...] = (
    "kind", "at", "trigger_id", "conversation_id", "customer_id",
    "segment", "detail", "value",
)

SEGMENT_FIELDS: tuple[str, ...] = ("customer_type", "region", "language")


def as_row(event: OutcomeEvent) -> dict[str, Any]:
    """Flat dict for CSV export (§2). Aggregate-safe: no transcript, no text."""
    return {
        "kind": event.kind.value,
        "at": event.at.isoformat(),
        "trigger_id": event.trigger_id or "",
        "conversation_id": event.conversation_id or "",
        "customer_id": event.customer_id or "",
        "customer_type": event.segment.customer_type or "",
        "region": event.segment.region or "",
        "language": event.segment.language or "",
        "detail": event.detail or "",
        "value": "" if event.value is None else event.value,
    }
