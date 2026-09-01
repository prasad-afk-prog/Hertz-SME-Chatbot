"""HTTP booking-API client + auth (M10 §5.3) — POA/10 §4.

Closes POA/10 §5 task 3. Sits behind the same `BookingAPIClient` protocol as
`MockClientAdapter`, so the service, breaker and cache are unchanged.

**The endpoint shapes are an assumption, and POA/10 §10.1 is still open.** Rather
than block, the request/response shape is confined to two small seams —
`BookingAPIEndpoints` (paths and query names) and the two `_parse_*` methods —
so adapting to the real contract is a config change plus two functions, not a
rewrite. Every assumption is stated in `BookingAPIEndpoints`.

**Transport is injected.** `Transport` is a protocol with a stdlib
`UrllibTransport` behind it. Two reasons: no new dependency for something the
standard library covers, and tests drive real HTTP semantics — 500s, timeouts,
malformed bodies — through a stub without a socket or a sleep.

**Auth covers the three plausible models** (§10.3 is open): bearer token, API-key
header, and HMAC request signing. Credentials come from the environment and are
never logged — `__repr__` is overridden on the auth types, because a
`BookingAPIConfig` in a stack trace would otherwise print the secret.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, runtime_checkable

from .client import BookingAPITimeout, BookingAPIUnavailable, NoDataForKey


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
@dataclass
class HTTPResponse:
    status: int
    body: str


@runtime_checkable
class Transport(Protocol):
    def get(self, url: str, headers: dict[str, str], timeout: float) -> HTTPResponse: ...


class UrllibTransport:
    """Stdlib transport — no new dependency for a handful of GETs."""

    def get(self, url: str, headers: dict[str, str], timeout: float) -> HTTPResponse:
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HTTPResponse(status=response.status, body=response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:            # 4xx/5xx still carry a body
            return HTTPResponse(status=exc.code, body=exc.read().decode("utf-8", "replace"))
        except TimeoutError as exc:
            raise BookingAPITimeout(str(exc)) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise BookingAPITimeout(str(exc)) from exc
            raise BookingAPIUnavailable(f"connection error: {exc.reason}") from exc


# --------------------------------------------------------------------------- #
# Auth (§10.3 is open — all three plausible models are supported)
# --------------------------------------------------------------------------- #
class Auth(Protocol):
    def headers(self, method: str, path: str, body: str = "") -> dict[str, str]: ...


@dataclass
class BearerAuth:
    token: str

    def headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def __repr__(self) -> str:            # never let a token into a stack trace
        return "BearerAuth(token='***')"


@dataclass
class APIKeyAuth:
    api_key: str
    header_name: str = "X-API-Key"

    def headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        return {self.header_name: self.api_key}

    def __repr__(self) -> str:
        return f"APIKeyAuth(api_key='***', header_name={self.header_name!r})"


@dataclass
class HMACAuth:
    """Signed requests: `HMAC-SHA256(secret, "METHOD\\nPATH\\nTIMESTAMP\\nBODY")`."""
    key_id: str
    secret: str
    clock: Any = time.time

    def headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        timestamp = str(int(self.clock()))
        payload = f"{method}\n{path}\n{timestamp}\n{body}".encode()
        signature = hmac.new(self.secret.encode(), payload, hashlib.sha256).hexdigest()
        return {
            "X-Key-Id": self.key_id,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }

    def __repr__(self) -> str:
        return f"HMACAuth(key_id={self.key_id!r}, secret='***')"


class NoAuth:
    def headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        return {}


def auth_from_env(prefix: str = "BOOKING_API") -> Auth:
    """Build auth from the environment. Never takes credentials as literals."""
    if token := os.environ.get(f"{prefix}_BEARER_TOKEN"):
        return BearerAuth(token)
    if api_key := os.environ.get(f"{prefix}_KEY"):
        return APIKeyAuth(api_key, os.environ.get(f"{prefix}_KEY_HEADER", "X-API-Key"))
    key_id = os.environ.get(f"{prefix}_HMAC_KEY_ID")
    secret = os.environ.get(f"{prefix}_HMAC_SECRET")
    if key_id and secret:
        return HMACAuth(key_id, secret)
    return NoAuth()


# --------------------------------------------------------------------------- #
# Endpoint shape — the assumption seam
# --------------------------------------------------------------------------- #
@dataclass
class BookingAPIEndpoints:
    """ASSUMED contract, pending POA/10 §10.1.

    Assumptions, all overridable without touching the client:
      * two GET endpoints, rate and availability;
      * query parameters name the location, class and date;
      * JSON responses carrying a scalar under a known key;
      * 404 means "no data for that key", not an outage.
    """
    rate_path: str = "/v1/rates"
    availability_path: str = "/v1/availability"
    location_param: str = "location"
    vehicle_class_param: str = "vehicle_class"
    date_param: str = "date"
    rate_field: str = "daily_rate"
    availability_field: str = "available"


@dataclass
class BookingAPIConfig:
    base_url: str = "https://booking.example.invalid"
    timeout_s: float = 1.0            # inline before delivery (§3.4)
    endpoints: BookingAPIEndpoints = field(default_factory=BookingAPIEndpoints)


class HTTPBookingAPIClient:
    """Real `BookingAPIClient` over HTTP.

    Every failure maps onto the three exceptions M10 already handles, so the
    service's breaker/cache logic is identical whether it is talking to the mock
    or to a live API:

      * timeout                  -> BookingAPITimeout   (§3.4: strip the claim)
      * 5xx / 401 / 403 / network -> BookingAPIUnavailable (counts to the breaker)
      * 404 / unparseable value   -> NoDataForKey      (does NOT trip the breaker)
    """

    def __init__(
        self,
        config: BookingAPIConfig | None = None,
        auth: Auth | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.config = config or BookingAPIConfig()
        self.auth = auth or auth_from_env()
        self.transport = transport or UrllibTransport()

    def _get(self, path: str, params: dict[str, str]) -> Any:
        query = urllib.parse.urlencode(params)
        full_path = f"{path}?{query}"
        url = f"{self.config.base_url.rstrip('/')}{full_path}"
        headers = {"Accept": "application/json", **self.auth.headers("GET", path)}

        response = self.transport.get(url, headers, self.config.timeout_s)

        if response.status == 404:
            raise NoDataForKey(f"404 for {path} {params}")
        if response.status in (401, 403):
            raise BookingAPIUnavailable(f"auth rejected ({response.status}) — check credentials")
        if response.status == 429:
            raise BookingAPIUnavailable("rate limited by booking API")
        if response.status >= 500:
            raise BookingAPIUnavailable(f"booking API returned {response.status}")
        if response.status != 200:
            raise BookingAPIUnavailable(f"unexpected status {response.status}")

        try:
            return json.loads(response.body)
        except (ValueError, TypeError) as exc:
            # A 200 we cannot read is a broken dependency, not a bad lookup.
            raise BookingAPIUnavailable(f"malformed JSON from booking API: {exc}") from exc

    def rate(self, location_id: str, vehicle_class: str, on: date) -> Decimal:
        e = self.config.endpoints
        payload = self._get(e.rate_path, {
            e.location_param: location_id,
            e.vehicle_class_param: vehicle_class,
            e.date_param: on.isoformat(),
        })
        value = payload.get(e.rate_field) if isinstance(payload, dict) else None
        if value is None:
            raise NoDataForKey(f"no {e.rate_field} for {location_id}/{vehicle_class} on {on}")
        try:
            return Decimal(str(value))
        except InvalidOperation as exc:
            raise NoDataForKey(f"unparseable rate {value!r}") from exc

    def availability(self, location_id: str, vehicle_class: str, on: date) -> int:
        e = self.config.endpoints
        payload = self._get(e.availability_path, {
            e.location_param: location_id,
            e.vehicle_class_param: vehicle_class,
            e.date_param: on.isoformat(),
        })
        value = payload.get(e.availability_field) if isinstance(payload, dict) else None
        if value is None:
            raise NoDataForKey(f"no {e.availability_field} for {location_id}/{vehicle_class} on {on}")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise NoDataForKey(f"unparseable availability {value!r}") from exc
