"""Direct tests of the trust-critical path (M10): the booking-API mock verifies
against the world, and resolution guarantees no unverified claim is delivered.
"""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal

from generator.models import BookingClaim, ClaimKind, VerifyStatus
from generator.reference import apply_verification
from mocks import BookingAPIMock


def _price_claim(world, loc, vc, quoted: Decimal, token: str) -> BookingClaim:
    pickup_at = _dt(world)
    return BookingClaim(
        kind=ClaimKind.price,
        pickup=loc,
        dropoff=loc,
        pickup_at=pickup_at,
        return_at=pickup_at,
        vehicle_class=vc,
        quoted_price=quoted,
        text_token=token,
    )


def _dt(world):
    from datetime import datetime

    d = world.start
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=timezone.utc)


def test_correct_price_passes(world):
    loc, vc = "LHR", "ICAR"
    rate = world.rate(loc, vc, world.start)
    claim = _price_claim(world, loc, vc, rate, f"£{rate:.2f}")
    res = BookingAPIMock(world).verify(claim)
    assert res.status == VerifyStatus.ok


def test_wrong_price_is_flagged_and_corrected(world):
    loc, vc = "LHR", "ICAR"
    rate = world.rate(loc, vc, world.start)
    wrong = rate - Decimal("15.00")
    token = f"£{wrong:.2f}"
    claim = _price_claim(world, loc, vc, wrong, token)
    res = BookingAPIMock(world).verify(claim)
    assert res.status == VerifyStatus.wrong
    delivered, _kind = apply_verification(f"Only {token}/day!", [claim], [res])
    assert token not in delivered            # wrong price removed
    assert f"£{rate:.2f}" in delivered       # replaced with the live price


def test_unverifiable_is_stripped_on_api_failure(world):
    loc, vc = "LHR", "ICAR"
    rate = world.rate(loc, vc, world.start)
    token = f"£{rate:.2f}"
    claim = _price_claim(world, loc, vc, rate, token)
    api = BookingAPIMock(world)
    api.force_failure(loc, vc, world.start)
    res = api.verify(claim)
    assert res.status == VerifyStatus.unverifiable
    delivered, _kind = apply_verification(f"Still {token}/day", [claim], [res])
    assert token not in delivered            # even a 'correct' price is stripped when unverifiable
