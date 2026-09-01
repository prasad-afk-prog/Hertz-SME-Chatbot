"""Claim detection (M10 node AA) — POA/10 §3.1.

Two detectors behind one protocol, and the choice between them is the whole
point of the module:

* **`TaggedClaimDetector` is the primary path.** POA/10 §3.1 recommends that
  M09 emit factual claims as structured metadata alongside the prose, so
  detection is *exact* rather than parsed back out of text. When claims arrive
  tagged, there is nothing to guess: the claim carries its own `text_token`, the
  precise substring that expresses it.

* **`PatternClaimDetector` is the fallback**, for text that arrives untagged.
  POA/10 §8 names "detector misses a claim (regex brittle)" as the first risk,
  and that risk is real: this detector is *known to be incomplete*. Rather than
  pretend otherwise, `tests/test_claim_verification_service.py` pins both what
  it catches and what it misses, so the gap is visible instead of assumed away.
  A fallback detector that quietly under-detects is worse than none, because it
  looks like coverage.

A claim this layer misses is a claim that reaches the customer unverified —
which is why the tagged path is primary and the pattern path exists only to
degrade gracefully.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol, runtime_checkable

from generator.models import BookingClaim, ClaimKind


@runtime_checkable
class ClaimDetector(Protocol):
    """Finds the factual claims in a draft response."""

    def detect(self, text: str, tagged: list[BookingClaim] | None = None) -> list[BookingClaim]: ...


class TaggedClaimDetector:
    """Primary path: trust the structured claims M09 emitted.

    Returns them unchanged, but drops any whose `text_token` is not actually in
    the draft — a tag that does not correspond to the text cannot be resolved
    against it, and silently keeping it would make the rewrite a no-op while
    reporting success.
    """

    def detect(self, text: str, tagged: list[BookingClaim] | None = None) -> list[BookingClaim]:
        return [c for c in (tagged or []) if c.text_token in text]


# Currency amounts: "£42.21", "€38", "from £42.21", "£42.21/day".
_MONEY = re.compile(r"[£€]\s?\d+(?:[.,]\d{2})?")
# Availability phrasing, positive and negative.
_AVAILABILITY = re.compile(
    r"\b(?:still |currently |now )?(?:available|in stock|not available|unavailable|sold out)\b",
    re.IGNORECASE,
)


class PatternClaimDetector:
    """Fallback path for untagged text. KNOWN TO BE INCOMPLETE — see module docs.

    It finds currency amounts and availability phrasing. It cannot recover the
    route, dates or vehicle class from prose, so the caller must supply that
    context; without it a detected amount cannot be looked up and the claim is
    reported as undetectable rather than silently dropped.
    """

    def __init__(
        self,
        pickup: str | None = None,
        dropoff: str | None = None,
        pickup_at: datetime | None = None,
        return_at: datetime | None = None,
        vehicle_class: str | None = None,
    ) -> None:
        self._ctx = (pickup, dropoff, pickup_at, return_at, vehicle_class)

    @property
    def has_context(self) -> bool:
        return all(v is not None for v in self._ctx)

    def detect(self, text: str, tagged: list[BookingClaim] | None = None) -> list[BookingClaim]:
        if tagged:                       # never override the exact path
            return TaggedClaimDetector().detect(text, tagged)
        if not self.has_context:
            # An amount with no route/dates cannot be verified. Returning [] here
            # would look like "no claims"; the service checks `has_context` and
            # treats a contextless draft containing money as unverifiable.
            return []

        pickup, dropoff, pickup_at, return_at, vehicle_class = self._ctx
        claims: list[BookingClaim] = []

        for match in _MONEY.finditer(text):
            token = match.group(0)
            try:
                amount = Decimal(re.sub(r"[£€\s]", "", token).replace(",", "."))
            except InvalidOperation:      # pragma: no cover - regex shape prevents this
                continue
            claims.append(
                BookingClaim(
                    kind=ClaimKind.price,
                    pickup=pickup, dropoff=dropoff,
                    pickup_at=pickup_at, return_at=return_at,
                    vehicle_class=vehicle_class,
                    quoted_price=amount,
                    text_token=token,
                )
            )

        for match in _AVAILABILITY.finditer(text):
            token = match.group(0)
            negative = any(w in token.lower() for w in ("not ", "un", "sold"))
            claims.append(
                BookingClaim(
                    kind=ClaimKind.availability,
                    pickup=pickup, dropoff=dropoff,
                    pickup_at=pickup_at, return_at=return_at,
                    vehicle_class=vehicle_class,
                    quoted_available=not negative,
                    text_token=token,
                )
            )

        return claims


def mentions_money(text: str) -> bool:
    """Cheap guard: does this draft look like it asserts a price at all?

    Used to catch the dangerous case of a contextless draft that contains an
    amount — see `PatternClaimDetector.detect`.
    """
    return bool(_MONEY.search(text))
