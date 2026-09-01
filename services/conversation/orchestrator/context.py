"""Context assembly (M08 node T) — POA/08 §3.1, and POA/15 §4's PII discipline.

**The PII claim this module makes, stated precisely.** POA/08 §8's fourth risk is
"PII sent to LLM", and S4 (POA/16 §16.5) built `reference.redact(text, spans)` —
which *applies* redaction but does not *detect* it. M08 has no detector, and
inventing one here would repeat the mistake S4 avoided: a detector that quietly
under-detects looks like coverage.

So the guarantee here is the smaller, checkable one, and it matches POA/15 §4's
"field allow-lists at ingestion": **the context bundle carries only an
allow-listed set of fields, and every field `pii.PII_FIELDS` marks as PII is
excluded by construction.** A test asserts the allow-list and `PII_FIELDS` do not
intersect, so adding a PII-bearing field to the bundle fails the suite rather
than quietly shipping a customer's name to a provider.

That covers *generated* context, which is all M08 assembles today. Free-text
customer content is a different problem: if it ever enters the bundle, it needs a
detector plus `redact()`, and the allow-list alone will not save it. `FREE_TEXT_
FIELDS` is deliberately empty and asserted empty, so that day is a failing test
rather than a silent regression.

§10.1 (which HFB service supplies profile and booking history) is open, so
`ProfileAdapter` is a protocol. `DatasetProfileAdapter` reads the generated
dataset today; a real HFB client slots in behind the same two methods.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from generator.models import Booking, Customer, Event, TriggerConfig
from generator.pii import PII_FIELDS
from services.common.resilience import Clock, TTLCache

# Fields permitted into a context bundle. Everything here is an identifier, a
# coarse attribute, or a code — nothing that identifies a natural person.
ALLOWED_CUSTOMER_FIELDS = ("customer_id", "customer_type", "region", "language", "segment")
ALLOWED_BOOKING_FIELDS = ("pickup", "dropoff", "vehicle_class", "pickup_at", "return_at", "status")
ALLOWED_SIGNAL_FIELDS = ("signal_type", "occurred_at", "pickup", "dropoff", "vehicle_class", "step")

#: Free-text fields would need a PII detector before reaching a provider.
#: Deliberately empty, and asserted empty — see the module docstring.
FREE_TEXT_FIELDS: tuple[str, ...] = ()

#: Every field name any PII-bearing model marks as PII. Nothing here may appear
#: in a bundle; the test asserts the two sets are disjoint.
PII_FIELD_NAMES = frozenset(
    field_name for fields in PII_FIELDS.values() for field_name in fields
)


@dataclass
class ContextBundle:
    """POA/08 §3.1's bundle. Plain data — everything in it is safe to send."""
    trigger_id: str
    signal_type: str
    customer: dict[str, str] = field(default_factory=dict)
    booking_history: list[dict[str, str]] = field(default_factory=list)
    recent_signals: list[dict[str, str]] = field(default_factory=list)
    personalisation: dict[str, str] = field(default_factory=dict)
    template_ref: str | None = None
    degraded: bool = False          # a lookup failed; we proceeded without it

    def all_values(self) -> list[str]:
        """Every string that will reach the prompt — used by the PII assertions."""
        out = list(self.customer.values())
        for row in (*self.booking_history, *self.recent_signals):
            out.extend(row.values())
        return [v for v in out if isinstance(v, str)]


@runtime_checkable
class ProfileAdapter(Protocol):
    """§10.1 is open — which HFB service backs this is not yet decided."""

    def customer(self, customer_id: str) -> Customer | None: ...
    def bookings(self, customer_id: str, limit: int = 3) -> list[Booking]: ...


class DatasetProfileAdapter:
    """Phase-1 adapter over the generated dataset."""

    def __init__(self, customers: list[Customer], bookings: list[Booking]) -> None:
        self._customers = {c.customer_id: c for c in customers}
        self._bookings: dict[str, list[Booking]] = {}
        for booking in bookings:
            self._bookings.setdefault(booking.customer_id, []).append(booking)

    def customer(self, customer_id: str) -> Customer | None:
        return self._customers.get(customer_id)

    def bookings(self, customer_id: str, limit: int = 3) -> list[Booking]:
        found = sorted(
            self._bookings.get(customer_id, []), key=lambda b: b.pickup_at, reverse=True
        )
        return found[:limit]


def _project(model: Any, allowed: tuple[str, ...]) -> dict[str, str]:
    """Copy only allow-listed fields, stringified. The allow-list is the PII
    guarantee, so this never falls back to `__dict__` or `model_dump()`."""
    out: dict[str, str] = {}
    for name in allowed:
        value = getattr(model, name, None)
        if value is None:
            continue
        out[name] = value.value if hasattr(value, "value") else str(value)
    return out


class ContextAssembler:
    """Builds the bundle (T), with caching and a hard rule about failure.

    §8's first risk is slow profile lookups blowing the latency budget. A lookup
    that fails or is missing does **not** abort: the bundle is marked `degraded`
    and assembly continues, because §6 requires the customer receives something
    safe rather than nothing. A personalised message is better than a generic
    one; a generic one is far better than silence.
    """

    def __init__(
        self,
        profiles: ProfileAdapter,
        *,
        history_limit: int = 3,
        cache_ttl_s: float = 60.0,
        clock: Clock = time.monotonic,
    ) -> None:
        self.profiles = profiles
        self.history_limit = history_limit
        self.cache = TTLCache(ttl_s=cache_ttl_s, clock=clock)

    def _customer(self, customer_id: str) -> Customer | None:
        cached = self.cache.get(("customer", customer_id))
        if cached is not None:
            return cached  # type: ignore[return-value]
        found = self.profiles.customer(customer_id)
        if found is not None:
            self.cache.put(("customer", customer_id), found)
        return found

    def assemble(
        self,
        trigger: TriggerConfig,
        customer_id: str,
        signals: list[Event] | None = None,
    ) -> ContextBundle:
        degraded = False

        try:
            customer = self._customer(customer_id)
        except Exception:
            customer, degraded = None, True
        if customer is None:
            degraded = True

        try:
            bookings = self.profiles.bookings(customer_id, self.history_limit)
        except Exception:
            bookings, degraded = [], True

        bundle = ContextBundle(
            trigger_id=trigger.trigger_id,
            signal_type=trigger.match.signal_type.value,
            customer=_project(customer, ALLOWED_CUSTOMER_FIELDS) if customer else {},
            booking_history=[_project(b, ALLOWED_BOOKING_FIELDS) for b in bookings],
            recent_signals=[
                {**_project(e, ALLOWED_SIGNAL_FIELDS),
                 **_project(e.context, ALLOWED_SIGNAL_FIELDS)}
                for e in (signals or [])
            ],
            template_ref=trigger.message_template_ref,
            degraded=degraded,
        )
        return bundle
