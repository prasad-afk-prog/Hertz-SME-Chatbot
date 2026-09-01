"""Claim Verification Service (M10) — POA/10, flow nodes AA -> AB -> AC.

The truthfulness guarantee: no price, rate or availability claim reaches a
customer unverified.

    detect (AA)  ->  verify against the live booking API (AB)  ->  resolve (AC)

**Resolution delegates to `generator.reference.apply_verification`, and that is
deliberate.** That function is the executable spec of the rewrite policy, and
`tests/test_invariants.py` ("no unverified claim ever delivered") and
`tests/test_golden_scenarios.py` already assert against it. If this service
reimplemented the policy, those suites would silently stop covering the code
that actually ships and there would be two competing definitions of correct.
This module owns detection, the API edge, caching, the breaker and the audit
log; the rewrite decision has one home.

What this service adds over the reference function:

* **Claim detection** (§3.1) — tagged claims preferred, pattern fallback.
* **The API edge** (§3.2, §3.4) — timeout, circuit breaker, short-TTL cache.
  Any failure becomes UNVERIFIABLE, so the claim is stripped and a safe message
  still goes out. Nothing blocks delivery.
* **Audit logging** (§2, feeding M14) — every claim, its outcome, and any
  correction. The log distinguishes an *outage* from *no data for that key*;
  both are UNVERIFIABLE to the customer, but they are different operational
  facts and M14 needs to tell them apart.
* **The §3.3 guardrail** — corrections must not introduce new unverified
  claims. Checked by counting token occurrences rather than testing
  containment, because a replacement may legitimately contain the token it
  replaced ("available" -> "not currently available"). See
  `VerifiedResponse.introduced_claims`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from generator.models import BookingClaim, ClaimKind, MessageKind, VerifyResult, VerifyStatus
from generator.reference import _STRIP_PHRASE, apply_verification

from .client import (
    BookingAPIClient,
    BookingAPITimeout,
    BookingAPIUnavailable,
    CircuitBreaker,
    Clock,
    NoDataForKey,
    TTLCache,
)
from .detection import ClaimDetector, TaggedClaimDetector, mentions_money
from .tolerance import DEFAULT_POLICY, TolerancePolicy


class FailureKind(str, Enum):
    """Why a claim could not be verified. Customer-facing outcome is identical
    (the claim is stripped); the operational meaning is not."""
    none = "none"
    timeout = "timeout"           # API too slow — inline deadline breached
    unavailable = "unavailable"   # outage / auth failure
    breaker_open = "breaker_open"  # we did not even call: API known bad
    no_data = "no_data"           # API answered; nothing for that key
    no_context = "no_context"     # draft asserts a price we cannot even look up


@dataclass
class VerificationRecord:
    """One claim's audit trail (§2, → M14)."""
    claim: BookingClaim
    status: VerifyStatus
    failure_kind: FailureKind = FailureKind.none
    quoted: Decimal | bool | None = None
    actual: Decimal | bool | None = None
    correction: str | None = None
    cache_hit: bool = False
    # A verification outcome is only meaningful alongside the rule that produced it.
    tolerance_rule: str | None = None

    @property
    def was_corrected(self) -> bool:
        return self.status is VerifyStatus.wrong


@dataclass
class VerifiedResponse:
    """The outcome of node AC."""
    delivered_text: str
    message_kind: MessageKind
    records: list[VerificationRecord] = field(default_factory=list)
    # §3.3 guardrail: claims present in the delivered text that were NOT
    # verified-ok. Must always be empty — a correction that introduces a new
    # unverified claim is the failure mode this exists to catch.
    introduced_claims: list[str] = field(default_factory=list)
    blocked: bool = False          # true when we refused to deliver at all

    @property
    def all_claims_resolved(self) -> bool:
        return not self.introduced_claims


class ClaimVerificationService:
    def __init__(
        self,
        client: BookingAPIClient,
        detector: ClaimDetector | None = None,
        *,
        tolerance: Decimal | str | None = None,
        tolerance_policy: TolerancePolicy | None = None,
        cache_ttl_s: float = 30.0,
        breaker_threshold: int = 3,
        breaker_cooldown_s: float = 30.0,
        clock: Clock = time.monotonic,
    ) -> None:
        self.client = client
        self.detector = detector or TaggedClaimDetector()
        # A bare `tolerance=` still works (absolute mode); `tolerance_policy=`
        # selects a mode once POA/10 §10.2 is answered.
        if tolerance_policy is not None:
            self.tolerance_policy = tolerance_policy
        elif tolerance is not None:
            self.tolerance_policy = TolerancePolicy(absolute=Decimal(tolerance))
        else:
            self.tolerance_policy = DEFAULT_POLICY
        self.cache = TTLCache(ttl_s=cache_ttl_s, clock=clock)
        self.breaker = CircuitBreaker(
            threshold=breaker_threshold, cooldown_s=breaker_cooldown_s, clock=clock
        )

    # ---- AB: verify one claim ------------------------------------------ #
    def _verify(self, claim: BookingClaim) -> tuple[VerifyResult, FailureKind, bool]:
        on = claim.pickup_at.date()
        kind = "rate" if claim.kind in (ClaimKind.price, ClaimKind.rate) else "avail"
        key = (kind, claim.pickup, claim.vehicle_class, on)

        cached = self.cache.get(key)
        if cached is not None:
            return self._compare(claim, cached), FailureKind.none, True

        if self.breaker.is_open:
            # Fail fast: the API is known bad, so do not pay the timeout.
            return VerifyResult(status=VerifyStatus.unverifiable), FailureKind.breaker_open, False

        try:
            actual = (
                self.client.rate(claim.pickup, claim.vehicle_class, on)
                if kind == "rate"
                else self.client.availability(claim.pickup, claim.vehicle_class, on)
            )
        except BookingAPITimeout:
            self.breaker.record_failure()
            return VerifyResult(status=VerifyStatus.unverifiable), FailureKind.timeout, False
        except BookingAPIUnavailable:
            self.breaker.record_failure()
            return VerifyResult(status=VerifyStatus.unverifiable), FailureKind.unavailable, False
        except NoDataForKey:
            # The API is healthy — this is a bad lookup, not an outage, so it
            # must NOT count towards opening the breaker.
            return VerifyResult(status=VerifyStatus.unverifiable), FailureKind.no_data, False

        self.breaker.record_success()
        self.cache.put(key, actual)          # successes only — never cache a failure
        return self._compare(claim, actual), FailureKind.none, False

    def _compare(self, claim: BookingClaim, actual) -> VerifyResult:
        if claim.kind in (ClaimKind.price, ClaimKind.rate):
            actual_price: Decimal = actual
            if claim.quoted_price is not None and self.tolerance_policy.accepts(
                claim.quoted_price, actual_price
            ):
                return VerifyResult(status=VerifyStatus.ok, correct_price=actual_price)
            return VerifyResult(
                status=VerifyStatus.wrong,
                correct_token=f"£{actual_price:.2f}",
                correct_price=actual_price,
            )
        actually_available = actual > 0
        if claim.quoted_available == actually_available:
            return VerifyResult(status=VerifyStatus.ok, correct_available=actually_available)
        return VerifyResult(
            status=VerifyStatus.wrong,
            correct_token="available" if actually_available else "not currently available",
            correct_available=actually_available,
        )

    # ---- AA -> AB -> AC ------------------------------------------------- #
    def verify_response(
        self,
        text: str,
        tagged_claims: list[BookingClaim] | None = None,
    ) -> VerifiedResponse:
        """Run the full node AA -> AB -> AC path over one draft response."""
        claims = self.detector.detect(text, tagged_claims)

        if not claims:
            # A draft with a money amount but no detectable claim is the
            # dangerous case: we cannot verify what we cannot address. Refuse
            # rather than deliver an unverified price (§2 hard rule).
            if mentions_money(text):
                return VerifiedResponse(
                    delivered_text="",
                    message_kind=MessageKind.fallback,
                    records=[],
                    blocked=True,
                )
            return VerifiedResponse(delivered_text=text, message_kind=MessageKind.llm)

        results: list[VerifyResult] = []
        records: list[VerificationRecord] = []
        for claim in claims:
            result, failure_kind, cache_hit = self._verify(claim)
            results.append(result)
            records.append(
                VerificationRecord(
                    claim=claim,
                    status=result.status,
                    failure_kind=failure_kind,
                    quoted=claim.quoted_price if claim.quoted_price is not None else claim.quoted_available,
                    actual=result.correct_price if result.correct_price is not None else result.correct_available,
                    correction=result.correct_token,
                    cache_hit=cache_hit,
                    tolerance_rule=self.tolerance_policy.describe(),
                )
            )

        # AC — one home for the rewrite policy.
        delivered, message_kind = apply_verification(text, claims, results)

        return VerifiedResponse(
            delivered_text=delivered,
            message_kind=message_kind,
            records=records,
            introduced_claims=self._surviving_unverified(text, delivered, claims, results),
        )

    # ---- §3.3 guardrail -------------------------------------------------- #
    @staticmethod
    def _surviving_unverified(
        draft: str, delivered: str, claims: list[BookingClaim], results: list[VerifyResult]
    ) -> list[str]:
        """Claim tokens that genuinely still assert an unverified fact.

        This is the assertion behind "corrections must not introduce new
        unverified claims" (§3.3) and the red-team requirement in §7. A
        correction substitutes a value that came *from* the booking API, so it
        is verified by construction today — but this is the property a future
        rewrite would break, so it is checked rather than assumed.

        Counting, not containment. A replacement may legitimately *contain* the
        token it replaced — correcting "available" to "not currently available"
        is the obvious case — and a naive `token in delivered` check flags that
        as a survival when the claim was in fact corrected. Resolution is
        whole-string substitution, so every original occurrence is replaced;
        any occurrence left in the delivered text must have come from inside a
        replacement. More than that many means one genuinely survived.
        """
        survived: list[str] = []
        for claim, result in zip(claims, results):
            if result.status is VerifyStatus.ok:
                continue
            token = claim.text_token
            replacement = result.correct_token or _STRIP_PHRASE
            replaced_count = draft.count(token)
            expected_from_replacements = replacement.count(token) * replaced_count
            if delivered.count(token) > expected_from_replacements:
                survived.append(token)
        return survived
