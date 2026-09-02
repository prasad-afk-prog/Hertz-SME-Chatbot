"""Metric model (M14 tasks 3 & 4) — POA/14 §3.2.

**Every rate here names its denominator, because that is where this module gets
silently wrong.** A rate whose denominator is undocumented is a number nobody can
defend in a review, and the four in §3.2 do not share one:

| Rate | Numerator | Denominator | Source of the denominator |
|------|-----------|-------------|---------------------------|
| conversion | `converted` | **fired** | A6 approvals — an engagement that was reserved |
| engagement | `responded` | **delivered** | M11 receipts — only messages that reached someone |
| handoff | `handed_off` | **conversations** | M12 outcomes — conversations that actually started |
| suppression | `suppressed` | **matched** = fired + suppressed | A6 saw the match either way |

Conversion is deliberately over *fired*, not *delivered*: an engagement that was
approved and then failed to deliver is a conversion we lost, and hiding it in the
denominator would flatter the number.

**A zero denominator returns `None`, never `0.0`.** "0% conversion" and "no data"
are different facts, and a dashboard that renders them identically lies to whoever
is making a decision from it.

**Pending is a real bucket.** M12 leaves a resolved-but-not-yet-booked
conversation's terminal state as `None` (POA/12 §11), because it is neither a
conversion nor a non-conversion while it is still inside its attribution window.
Bucketing it as not-converted would under-report conversion for every live
conversation; dropping it would stop the funnel adding up. So it is counted, and
`converted + not_converted + pending == conversations` is asserted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .events import OutcomeKind


def rate(numerator: int, denominator: int) -> float | None:
    """A rate, or None when there is nothing to divide by.

    Returning 0.0 for an empty denominator is the classic reporting bug: it
    renders as "0%", which reads as a failing trigger rather than an unused one.
    """
    if denominator <= 0:
        return None
    return numerator / denominator


@dataclass
class Counts:
    """Raw counts for one slice. Every rate is derived from these, so the
    reconciliation test can recount from the store and compare."""
    fired: int = 0
    suppressed: int = 0
    delivered: int = 0
    delivery_failed: int = 0
    read: int = 0
    responded: int = 0
    no_engagement: int = 0
    resolved: int = 0
    converted: int = 0
    handed_off: int = 0
    fallback_used: int = 0
    claim_corrected: int = 0
    claim_stripped: int = 0
    deferred_expired: int = 0

    @property
    def matched(self) -> int:
        """A6 saw the signal whether it fired or was suppressed."""
        return self.fired + self.suppressed

    @property
    def conversations(self) -> int:
        """Conversations that actually started — someone replied, or the window
        closed with no reply. A failed delivery never became a conversation."""
        return self.responded + self.no_engagement

    @property
    def pending(self) -> int:
        """Resolved but not yet booked — see the module docstring.

        Clamped at zero: a conversion can arrive in a later window than its
        resolution, which would otherwise produce a negative bucket at the
        boundary and make the funnel nonsense.
        """
        return max(0, self.resolved - self.converted)


@dataclass
class Metrics:
    """§3.2's metric model. `None` anywhere means "no data", not zero."""
    counts: Counts = field(default_factory=Counts)

    @property
    def conversion_rate(self) -> float | None:
        return rate(self.counts.converted, self.counts.fired)

    @property
    def engagement_rate(self) -> float | None:
        return rate(self.counts.responded, self.counts.delivered)

    @property
    def handoff_rate(self) -> float | None:
        return rate(self.counts.handed_off, self.counts.conversations)

    @property
    def suppression_rate(self) -> float | None:
        return rate(self.counts.suppressed, self.counts.matched)

    @property
    def fallback_rate(self) -> float | None:
        """Quality signal — how often M09 could not use a generation."""
        return rate(self.counts.fallback_used, self.counts.delivered)

    @property
    def verification_correction_rate(self) -> float | None:
        """Quality signal — how often M10 had to correct or strip a claim.

        A rising number here means the model is increasingly asserting things
        that are not true, which is the failure the whole verification layer
        exists to catch. Worth an alert, not just a chart.
        """
        corrections = self.counts.claim_corrected + self.counts.claim_stripped
        return rate(corrections, self.counts.delivered)

    @property
    def delivery_failure_rate(self) -> float | None:
        attempted = self.counts.delivered + self.counts.delivery_failed
        return rate(self.counts.delivery_failed, attempted)

    def funnel(self) -> dict[str, int]:
        """§3.3's funnel: signal -> engaged -> resolved -> booked.

        Each stage is a subset of the one before it, and a test asserts that —
        a funnel that widens is a bug in the counting, not an insight.
        """
        return {
            "matched": self.counts.matched,
            "fired": self.counts.fired,
            "delivered": self.counts.delivered,
            "responded": self.counts.responded,
            "resolved": self.counts.resolved,
            "converted": self.counts.converted,
        }

    def outcome_split(self) -> dict[str, int]:
        """converted / not_converted / pending, which must sum to conversations."""
        converted = self.counts.converted
        pending = self.counts.pending
        return {
            "converted": converted,
            "pending": pending,
            "not_converted": max(0, self.counts.conversations - converted - pending),
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "counts": dict(self.counts.__dict__),
            "matched": self.counts.matched,
            "conversations": self.counts.conversations,
            "conversion_rate": self.conversion_rate,
            "engagement_rate": self.engagement_rate,
            "handoff_rate": self.handoff_rate,
            "suppression_rate": self.suppression_rate,
            "fallback_rate": self.fallback_rate,
            "verification_correction_rate": self.verification_correction_rate,
            "delivery_failure_rate": self.delivery_failure_rate,
            "funnel": self.funnel(),
            "outcome_split": self.outcome_split(),
        }


#: OutcomeKind -> the Counts attribute it increments. One place, so the rollup
#: and the reconciliation recount cannot drift.
KIND_TO_COUNT: dict[OutcomeKind, str] = {
    OutcomeKind.fired: "fired",
    OutcomeKind.suppressed: "suppressed",
    OutcomeKind.delivered: "delivered",
    OutcomeKind.delivery_failed: "delivery_failed",
    OutcomeKind.read: "read",
    OutcomeKind.responded: "responded",
    OutcomeKind.no_engagement: "no_engagement",
    OutcomeKind.resolved: "resolved",
    OutcomeKind.converted: "converted",
    OutcomeKind.handed_off: "handed_off",
    OutcomeKind.fallback_used: "fallback_used",
    OutcomeKind.claim_corrected: "claim_corrected",
    OutcomeKind.claim_stripped: "claim_stripped",
    OutcomeKind.deferred_expired: "deferred_expired",
}
