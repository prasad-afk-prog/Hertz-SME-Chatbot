"""Booking-API client edge (M10 node AB) — POA/10 §3.2, §3.4, §4.

Everything here exists to serve one hard rule from §3.4: verification is inline
before delivery, so a slow or broken booking API must **never** block delivery
and must **never** result in an unverified claim going out. Both failure paths
converge on UNVERIFIABLE, which the resolution step then strips.

Three concerns, deliberately separated:

* **`BookingAPIClient`** — the protocol. `MockClientAdapter` puts the existing
  `mocks/booking_api.py` behind it today; a real HTTP client (auth, retries)
  slots in later without the service changing. POA/10 §5.3's auth work has
  nothing to build against until the §10 endpoint questions are answered.

* **`CircuitBreaker`** — after repeated failures, stop calling a dead API and
  fail fast. Without it, an outage means every message pays the full timeout.

* **`TTLCache`** — §3.2's short-TTL cache for identical lookups.

**Time is injected everywhere.** Every timeout, TTL and breaker cooldown takes a
`clock` callable, so the tests assert expiry rather than sleeping for it. The
suite runs in well under a second and must stay that way; a wall-clock test here
would be both slow and flaky.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Callable, Protocol, runtime_checkable

from mocks.booking_api import BookingAPIFailure, BookingAPIMock
from services.common.resilience import Clock, CircuitBreaker, TTLCache

# Re-exported so callers (and M10's tests) keep importing them from here.
__all__ = [
    "BookingAPIClient", "BookingAPIError", "BookingAPITimeout", "BookingAPIUnavailable",
    "CacheKey", "CircuitBreaker", "Clock", "MockClientAdapter", "NoDataForKey", "TTLCache",
]


class BookingAPIError(Exception):
    """Base: the booking API could not answer."""


class BookingAPITimeout(BookingAPIError):
    """Lookup exceeded the inline deadline (§3.4)."""


class BookingAPIUnavailable(BookingAPIError):
    """Outage, auth failure, or the breaker is open."""


class NoDataForKey(BookingAPIError):
    """The API answered, but has no rate/availability for that key.

    Distinct from an outage on purpose: both become UNVERIFIABLE for the
    customer, but M14 needs to tell a broken dependency from a bad lookup.
    """


@runtime_checkable
class BookingAPIClient(Protocol):
    def rate(self, location_id: str, vehicle_class: str, on: date) -> Decimal: ...
    def availability(self, location_id: str, vehicle_class: str, on: date) -> int: ...


# --------------------------------------------------------------------------- #
# Adapter: the existing mock behind the client protocol
# --------------------------------------------------------------------------- #
class MockClientAdapter:
    """Puts `mocks.booking_api.BookingAPIMock` behind `BookingAPIClient`.

    `latency_s` is an injected *simulated* duration, not a real delay: it lets
    the timeout path be asserted deterministically. Returning a duration greater
    than the service's deadline raises `BookingAPITimeout` without any waiting.
    """

    def __init__(
        self,
        mock: BookingAPIMock,
        timeout_s: float = 1.0,
        latency_s: Callable[[], float] | float = 0.0,
    ) -> None:
        self._mock = mock
        self._timeout_s = timeout_s
        self._latency = latency_s if callable(latency_s) else (lambda: float(latency_s))

    def _check_deadline(self) -> None:
        if self._latency() > self._timeout_s:
            raise BookingAPITimeout(
                f"lookup exceeded {self._timeout_s}s deadline — treated as unverifiable (§3.4)"
            )

    def rate(self, location_id: str, vehicle_class: str, on: date) -> Decimal:
        self._check_deadline()
        try:
            return self._mock.rate(location_id, vehicle_class, on)
        except BookingAPIFailure as exc:
            raise BookingAPIUnavailable(str(exc)) from exc
        except KeyError as exc:
            raise NoDataForKey(f"no rate for {location_id}/{vehicle_class} on {on}") from exc

    def availability(self, location_id: str, vehicle_class: str, on: date) -> int:
        self._check_deadline()
        try:
            return self._mock.availability(location_id, vehicle_class, on)
        except BookingAPIFailure as exc:
            raise BookingAPIUnavailable(str(exc)) from exc
        except KeyError as exc:
            raise NoDataForKey(f"no availability for {location_id}/{vehicle_class} on {on}") from exc
