"""M10 Claim Verification Service — POA/10 acceptance criteria (§6) and the
adversarial tests §7 asks for.

The one guarantee everything here serves: **no unverified price, rate or
availability claim is ever delivered.** These tests try to break it.

Time is injected throughout — the timeout, TTL and breaker-cooldown tests assert
expiry rather than sleeping for it, so the suite stays deterministic and fast.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from generator.models import (
    BookingClaim,
    ClaimKind,
    FailureKey,
    MessageKind,
    VerifyStatus,
)
from mocks.booking_api import BookingAPIMock
from services.conversation.claim_verification import (
    ClaimVerificationService,
    FailureKind as FK,
    MockClientAdapter,
    PatternClaimDetector,
    TaggedClaimDetector,
)
from services.conversation.claim_verification.client import CircuitBreaker, TTLCache

_TZ = timezone.utc


class FakeClock:
    """Injected time. `advance()` moves it; nothing ever sleeps."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def loc_vc(world):
    return "LHR", "ICAR"


@pytest.fixture
def when(world):
    d = world.start
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=_TZ)


def make_claim(loc, vc, when, *, price=None, available=None, token="") -> BookingClaim:
    return BookingClaim(
        kind=ClaimKind.price if price is not None else ClaimKind.availability,
        pickup=loc, dropoff=loc,
        pickup_at=when, return_at=when + timedelta(days=3),
        vehicle_class=vc,
        quoted_price=price, quoted_available=available,
        text_token=token,
    )


def service(world, *, failures=None, clock=None, latency=0.0, timeout=1.0, **kw):
    mock = BookingAPIMock(world, failures=failures or [])
    client = MockClientAdapter(mock, timeout_s=timeout, latency_s=latency)
    return ClaimVerificationService(client, clock=clock or FakeClock(), **kw)


# --- §6: correct claims pass, non-factual passes through --------------------- #
def test_correct_price_passes_through_unchanged(world, loc_vc, when):
    loc, vc = loc_vc
    rate = world.rate(loc, vc, when.date())
    token = f"£{rate:.2f}"
    text = f"That Intermediate is {token}/day."
    svc = service(world)

    out = svc.verify_response(text, [make_claim(loc, vc, when, price=rate, token=token)])

    assert out.delivered_text == text
    assert out.message_kind is MessageKind.verified
    assert out.records[0].status is VerifyStatus.ok
    assert out.all_claims_resolved


def test_response_with_no_claims_passes_through(world):
    svc = service(world)
    text = "Happy to help — which airport are you collecting from?"
    out = svc.verify_response(text, [])
    assert out.delivered_text == text
    assert out.message_kind is MessageKind.llm
    assert not out.records


# --- §6: wrong claims are corrected to the live value ------------------------ #
def test_wrong_price_is_corrected_and_the_wrong_value_never_survives(world, loc_vc, when):
    loc, vc = loc_vc
    rate = world.rate(loc, vc, when.date())
    wrong = (rate - Decimal("10.00")).quantize(Decimal("0.01"))
    wrong_token = f"£{wrong:.2f}"
    svc = service(world)

    out = svc.verify_response(
        f"Good news — I can do that for just {wrong_token}/day!",
        [make_claim(loc, vc, when, price=wrong, token=wrong_token)],
    )

    assert wrong_token not in out.delivered_text, "the wrong price reached the customer"
    assert f"£{rate:.2f}" in out.delivered_text
    assert out.message_kind is MessageKind.corrected
    assert out.records[0].was_corrected
    assert out.records[0].actual == rate
    assert out.all_claims_resolved


def test_wrong_availability_is_corrected(world, when):
    sold_out = next(a for a in world.availability if a.date == when.date() and a.available == 0)
    loc, vc = sold_out.location_id, sold_out.vehicle_class
    svc = service(world)

    out = svc.verify_response(
        "That vehicle is available at your pickup location.",
        [make_claim(loc, vc, when, available=True, token="available")],
    )

    assert out.records[0].status is VerifyStatus.wrong
    assert "not currently available" in out.delivered_text
    assert out.all_claims_resolved


# --- §6: outage strips the claim, and a safe message still goes out ---------- #
def test_api_outage_strips_the_claim_but_still_delivers(world, loc_vc, when):
    loc, vc = loc_vc
    rate = world.rate(loc, vc, when.date())
    token = f"£{rate:.2f}"
    svc = service(world, failures=[FailureKey(location_id=loc, vehicle_class=vc, date=when.date())])

    out = svc.verify_response(
        f"No problem — that car is still {token}/day, want to try again?",
        [make_claim(loc, vc, when, price=rate, token=token)],
    )

    assert token not in out.delivered_text, "an unverifiable price was delivered"
    assert out.delivered_text, "outage must still deliver a safe message, not nothing"
    assert out.message_kind is MessageKind.stripped
    assert out.records[0].failure_kind is FK.unavailable
    assert out.all_claims_resolved


def test_timeout_is_treated_as_unverifiable_not_blocking(world, loc_vc, when):
    """§3.4: an inline deadline breach strips the claim; it never blocks."""
    loc, vc = loc_vc
    rate = world.rate(loc, vc, when.date())
    token = f"£{rate:.2f}"
    svc = service(world, latency=5.0, timeout=1.0)

    out = svc.verify_response(f"It's {token}/day.", [make_claim(loc, vc, when, price=rate, token=token)])

    assert out.records[0].failure_kind is FK.timeout
    assert token not in out.delivered_text
    assert out.delivered_text


def test_outage_and_no_data_are_distinguishable_in_the_log(world, loc_vc, when):
    """Same customer outcome, different operational fact — M14 needs both."""
    loc, vc = loc_vc
    rate = world.rate(loc, vc, when.date())

    outage = service(world, failures=[FailureKey(location_id=loc, vehicle_class=vc, date=when.date())])
    out1 = outage.verify_response("x £1.00", [make_claim(loc, vc, when, price=rate, token="£1.00")])

    missing = service(world)
    far_future = when + timedelta(days=3650)     # outside the generated window
    out2 = missing.verify_response(
        "x £1.00", [make_claim(loc, vc, far_future, price=rate, token="£1.00")]
    )

    assert out1.records[0].failure_kind is FK.unavailable
    assert out2.records[0].failure_kind is FK.no_data
    assert out1.records[0].status is out2.records[0].status is VerifyStatus.unverifiable


# --- §3.3 / §7: the red-team requirement ------------------------------------- #
def test_no_unverified_claim_ever_survives_across_every_branch(world, loc_vc, when):
    """The adversarial test §7 demands: try to get an unverified price through."""
    loc, vc = loc_vc
    rate = world.rate(loc, vc, when.date())
    wrong = (rate + Decimal("13.37")).quantize(Decimal("0.01"))

    cases = [
        ("correct", service(world), rate),
        ("wrong", service(world), wrong),
        ("outage", service(world, failures=[FailureKey(location_id=loc, vehicle_class=vc, date=when.date())]), rate),
        ("timeout", service(world, latency=9.0, timeout=0.5), rate),
    ]
    for name, svc, quoted in cases:
        token = f"£{quoted:.2f}"
        out = svc.verify_response(
            f"Your car is {token}/day.", [make_claim(loc, vc, when, price=quoted, token=token)]
        )
        assert out.all_claims_resolved, f"{name}: unverified claim survived {out.introduced_claims}"
        if name != "correct":
            assert token not in out.delivered_text, f"{name}: {token} was delivered"


def test_correction_does_not_introduce_a_new_unverified_claim(world, loc_vc, when):
    """§3.3. The corrected value comes FROM the booking API, so it is verified by
    construction — this pins the property a future rewrite would break."""
    loc, vc = loc_vc
    rate = world.rate(loc, vc, when.date())
    wrong = (rate - Decimal("5.00")).quantize(Decimal("0.01"))
    svc = service(world)

    out = svc.verify_response(
        f"It's £{wrong:.2f}/day.", [make_claim(loc, vc, when, price=wrong, token=f"£{wrong:.2f}")]
    )

    # Re-detect on the DELIVERED text and verify whatever is left.
    redetect = PatternClaimDetector(loc, loc, when, when + timedelta(days=3), vc)
    leftover = redetect.detect(out.delivered_text)
    for claim in leftover:
        again = svc.verify_response(out.delivered_text, [claim])
        assert again.records[0].status is VerifyStatus.ok, "correction left an unverified claim"


def test_money_with_no_verifiable_context_is_refused_not_delivered(world):
    """A draft quoting a price we cannot even look up must not go out."""
    svc = service(world, detector=PatternClaimDetector())     # no route/date context
    out = svc.verify_response("It's about £42.00 a day.")
    assert out.blocked
    assert out.delivered_text == ""


# --- detection ---------------------------------------------------------------- #
def test_tagged_detector_drops_tags_absent_from_the_text(world, loc_vc, when):
    loc, vc = loc_vc
    claim = make_claim(loc, vc, when, price=Decimal("42.00"), token="£42.00")
    assert TaggedClaimDetector().detect("no price here", [claim]) == []
    assert TaggedClaimDetector().detect("it is £42.00", [claim]) == [claim]


def test_pattern_detector_catches_common_price_and_availability_phrasings(world, loc_vc, when):
    loc, vc = loc_vc
    det = PatternClaimDetector(loc, loc, when, when + timedelta(days=3), vc)
    found = det.detect("From £42.21 per day, and it is still available.")
    kinds = {c.kind for c in found}
    assert ClaimKind.price in kinds and ClaimKind.availability in kinds
    assert any(c.text_token == "£42.21" for c in found)


def test_pattern_detector_known_gaps_are_pinned_not_assumed_away(world, loc_vc, when):
    """POA/10 §8's first risk. The fallback detector IS incomplete — recording
    exactly where keeps the gap visible and justifies tagged claims as primary."""
    loc, vc = loc_vc
    det = PatternClaimDetector(loc, loc, when, when + timedelta(days=3), vc)

    assert det.detect("that'll be forty-two pounds a day") == [], \
        "words-not-digits is a known gap — if this now passes, update the docs"
    assert det.detect("the price is 42.21 a day") == [], \
        "amount with no currency symbol is a known gap"


def test_pattern_detector_defers_to_tags_when_both_are_available(world, loc_vc, when):
    loc, vc = loc_vc
    det = PatternClaimDetector(loc, loc, when, when + timedelta(days=3), vc)
    tagged = [make_claim(loc, vc, when, price=Decimal("42.21"), token="£42.21")]
    assert det.detect("From £42.21 and £99.99 too", tagged) == tagged


# --- cache -------------------------------------------------------------------- #
def test_second_identical_lookup_is_served_from_cache(world, loc_vc, when):
    loc, vc = loc_vc
    rate = world.rate(loc, vc, when.date())
    token = f"£{rate:.2f}"
    svc = service(world)
    claim = make_claim(loc, vc, when, price=rate, token=token)

    first = svc.verify_response(f"It's {token}.", [claim])
    second = svc.verify_response(f"It's {token}.", [claim])

    assert not first.records[0].cache_hit
    assert second.records[0].cache_hit


def test_cache_expires_on_the_injected_clock(world, loc_vc, when):
    loc, vc = loc_vc
    rate = world.rate(loc, vc, when.date())
    token = f"£{rate:.2f}"
    clock = FakeClock()
    svc = service(world, clock=clock, cache_ttl_s=30.0)
    claim = make_claim(loc, vc, when, price=rate, token=token)

    svc.verify_response(f"It's {token}.", [claim])
    clock.advance(31.0)
    after = svc.verify_response(f"It's {token}.", [claim])
    assert not after.records[0].cache_hit


def test_cache_key_includes_the_date(world, loc_vc, when):
    """Dropping the date would serve yesterday's price for today — exactly the
    confidently-wrong answer M10 exists to prevent."""
    clock = FakeClock()
    cache = TTLCache(ttl_s=100.0, clock=clock)
    loc, vc = loc_vc
    d1, d2 = when.date(), (when + timedelta(days=1)).date()
    cache.put(("rate", loc, vc, d1), Decimal("10.00"))
    assert cache.get(("rate", loc, vc, d2)) is None
    assert cache.get(("rate", loc, vc, d1)) == Decimal("10.00")


def test_failures_are_never_cached(world, loc_vc, when):
    """A cached outage would poison verification for the whole TTL, even after
    the API recovers."""
    loc, vc = loc_vc
    rate = world.rate(loc, vc, when.date())
    token = f"£{rate:.2f}"
    mock = BookingAPIMock(world, failures=[FailureKey(location_id=loc, vehicle_class=vc, date=when.date())])
    client = MockClientAdapter(mock)
    svc = ClaimVerificationService(client, clock=FakeClock())
    claim = make_claim(loc, vc, when, price=rate, token=token)

    svc.verify_response(f"It's {token}.", [claim])
    assert len(svc.cache) == 0, "a failure was cached"


# --- circuit breaker ---------------------------------------------------------- #
def test_breaker_opens_after_repeated_failures_and_then_fails_fast(world, loc_vc, when):
    loc, vc = loc_vc
    rate = world.rate(loc, vc, when.date())
    token = f"£{rate:.2f}"
    clock = FakeClock()
    svc = service(
        world,
        failures=[FailureKey(location_id=loc, vehicle_class=vc, date=when.date())],
        clock=clock, breaker_threshold=3, cache_ttl_s=0.0,
    )
    claim = make_claim(loc, vc, when, price=rate, token=token)

    for _ in range(3):
        svc.verify_response(f"It's {token}.", [claim])
    assert svc.breaker.state == "open"

    out = svc.verify_response(f"It's {token}.", [claim])
    assert out.records[0].failure_kind is FK.breaker_open
    assert token not in out.delivered_text


def test_breaker_half_opens_after_cooldown_on_the_injected_clock():
    clock = FakeClock()
    cb = CircuitBreaker(threshold=2, cooldown_s=30.0, clock=clock)
    cb.record_failure(); cb.record_failure()
    assert cb.state == "open"
    clock.advance(31.0)
    assert cb.state == "half-open"
    cb.record_success()
    assert cb.state == "closed"


def test_no_data_does_not_trip_the_breaker(world, loc_vc, when):
    """A bad lookup is not a broken dependency; counting it would open the
    breaker on healthy infrastructure."""
    loc, vc = loc_vc
    clock = FakeClock()
    svc = service(world, clock=clock, breaker_threshold=2, cache_ttl_s=0.0)
    far = when + timedelta(days=3650)
    claim = make_claim(loc, vc, far, price=Decimal("50.00"), token="£50.00")

    for _ in range(5):
        svc.verify_response("It's £50.00.", [claim])
    assert svc.breaker.state == "closed"


# --- audit log (§2 -> M14) ----------------------------------------------------- #
def test_every_claim_produces_a_log_record(world, loc_vc, when):
    loc, vc = loc_vc
    rate = world.rate(loc, vc, when.date())
    wrong = (rate - Decimal("3.00")).quantize(Decimal("0.01"))
    svc = service(world)
    claims = [
        make_claim(loc, vc, when, price=rate, token=f"£{rate:.2f}"),
        make_claim(loc, vc, when, price=wrong, token=f"£{wrong:.2f}"),
    ]
    out = svc.verify_response(f"Either £{rate:.2f} or £{wrong:.2f}.", claims)

    assert len(out.records) == 2
    assert {r.status for r in out.records} == {VerifyStatus.ok, VerifyStatus.wrong}
    corrected = next(r for r in out.records if r.was_corrected)
    assert corrected.quoted == wrong and corrected.actual == rate
    assert corrected.correction == f"£{rate:.2f}"


def test_service_agrees_with_the_reference_implementation(world, loc_vc, when):
    """The service delegates resolution to reference.apply_verification, so the
    existing invariant and golden-scenario suites still cover what ships. This
    pins that the delegation is real and not a divergent copy."""
    from generator.reference import apply_verification

    loc, vc = loc_vc
    rate = world.rate(loc, vc, when.date())
    wrong = (rate - Decimal("8.00")).quantize(Decimal("0.01"))
    token = f"£{wrong:.2f}"
    text = f"It's {token}/day."
    claim = make_claim(loc, vc, when, price=wrong, token=token)

    svc = service(world)
    out = svc.verify_response(text, [claim])
    expected_text, expected_kind = apply_verification(
        text, [claim], [svc._compare(claim, rate)]
    )
    assert out.delivered_text == expected_text
    assert out.message_kind is expected_kind
