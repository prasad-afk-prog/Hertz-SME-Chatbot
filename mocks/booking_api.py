"""Booking-API mock + reference claim verifier (backs M10).

Reads price/availability from the seeded World. Can be forced to fail for
specific (location, class, date) keys to drive the 'unverifiable' branch.

`verify()` mirrors the M10 hard rule: any error/timeout during verification is
treated as UNVERIFIABLE (the claim will then be stripped), never passed through.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from generator.models import BookingClaim, ClaimKind, FailureKey, VerifyResult, VerifyStatus
from generator.world import World


class BookingAPIFailure(Exception):
    """Raised when a forced (timeout/outage/unavailable) key is queried."""


class BookingAPIMock:
    def __init__(self, world: World, failures: list[FailureKey] | None = None, tolerance: str = "0.01") -> None:
        self.world = world
        self.tolerance = Decimal(tolerance)
        self._forced = {(f.location_id, f.vehicle_class, f.date) for f in (failures or [])}

    def force_failure(self, location_id: str, vehicle_class: str, on: date) -> None:
        self._forced.add((location_id, vehicle_class, on))

    # --- raw endpoints -------------------------------------------------- #
    def rate(self, location_id: str, vehicle_class: str, on: date) -> Decimal:
        if (location_id, vehicle_class, on) in self._forced:
            raise BookingAPIFailure("forced failure")
        return self.world.rate(location_id, vehicle_class, on)

    def availability(self, location_id: str, vehicle_class: str, on: date) -> int:
        if (location_id, vehicle_class, on) in self._forced:
            raise BookingAPIFailure("forced failure")
        return self.world.availability_count(location_id, vehicle_class, on)

    # --- verification (M10) --------------------------------------------- #
    def verify(self, claim: BookingClaim) -> VerifyResult:
        on = claim.pickup_at.date()
        try:
            if claim.kind in (ClaimKind.price, ClaimKind.rate):
                actual = self.rate(claim.pickup, claim.vehicle_class, on)
                if claim.quoted_price is not None and abs(actual - claim.quoted_price) <= self.tolerance:
                    return VerifyResult(status=VerifyStatus.ok, correct_price=actual)
                return VerifyResult(
                    status=VerifyStatus.wrong,
                    correct_token=f"£{actual:.2f}",
                    correct_price=actual,
                )
            else:  # availability
                count = self.availability(claim.pickup, claim.vehicle_class, on)
                actually = count > 0
                if claim.quoted_available == actually:
                    return VerifyResult(status=VerifyStatus.ok, correct_available=actually)
                return VerifyResult(
                    status=VerifyStatus.wrong,
                    correct_token="available" if actually else "not currently available",
                    correct_available=actually,
                )
        except BookingAPIFailure:
            # M10 hard rule: unverifiable -> claim must be stripped, never sent
            return VerifyResult(status=VerifyStatus.unverifiable)
