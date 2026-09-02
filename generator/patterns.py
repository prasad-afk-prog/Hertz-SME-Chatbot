"""Signal-pattern generators — one per signal type (flowchart nodes C–J).

Each pattern emits the characteristic event(s) for its signal, always referencing
a real (location, vehicle, dates) drawn from the world so context stays
consistent (design principle P2).

Deferred signals:
  * repeated_search (I): emitted as two events, same search, across two sessions.
  * dormant (J): emitted as one derived candidate event; the customer's
    last_booking_at (set in CustomerFactory) is what actually makes them dormant.
    This mirrors M04 deferred-derivation producing a synthetic candidate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from random import Random
from typing import Protocol

from .clock import Clock
from .models import BookingStep, Event, EventContext, SignalType
from .rng import stable_uuid
from .world import World


@dataclass
class Search:
    pickup: str
    dropoff: str
    pickup_at: datetime
    return_at: datetime
    vehicle_class: str

    @property
    def on(self) -> date:
        return self.pickup_at.date()


@dataclass
class SessionCtx:
    customer_id: str
    session_id: str
    world: World
    rng: Random
    clock: Clock

    def pick_search(self) -> Search:
        """A booking search grounded in the world (rate/availability exist)."""
        loc = self.rng.choice(self.world.location_ids)
        vc = self.rng.choice(self.world.vehicle_codes)
        # a pickup date that exists in the world's rate calendar
        offset = self.rng.randint(0, self.world.days - 3)
        pickup_day = self.world.start + timedelta(days=offset)
        pickup_at = datetime(pickup_day.year, pickup_day.month, pickup_day.day, 10, 0, tzinfo=self.clock.now().tzinfo)
        return_at = pickup_at + timedelta(days=self.rng.randint(1, 5))
        return Search(loc, loc, pickup_at, return_at, vc)

    def event(self, signal: SignalType, search: Search | None, **ctx) -> Event:
        base = {}
        if search is not None:
            base = dict(
                pickup=search.pickup,
                dropoff=search.dropoff,
                pickup_at=search.pickup_at,
                return_at=search.return_at,
                vehicle_class=search.vehicle_class,
            )
        base.update(ctx)
        occurred_at = self.clock.tick(self.rng.randint(2, 30))
        return Event(
            event_id=stable_uuid(self.customer_id, self.session_id, signal.value, occurred_at.isoformat()),
            customer_id=self.customer_id,
            session_id=self.session_id,
            signal_type=signal,
            occurred_at=occurred_at,
            context=EventContext(**base),
        )


class SignalPattern(Protocol):
    signal: SignalType

    def emit(self, ctx: SessionCtx) -> list[Event]: ...


class SearchNoConvert:
    signal = SignalType.search_no_convert

    def emit(self, ctx: SessionCtx) -> list[Event]:
        return [ctx.event(self.signal, ctx.pick_search())]


class RateViewNoProgress:
    signal = SignalType.rate_view_no_progress

    def emit(self, ctx: SessionCtx) -> list[Event]:
        s = ctx.pick_search()
        return [
            ctx.event(SignalType.search_no_convert, s),
            ctx.event(self.signal, s),
        ]


class BookingAbandoned:
    signal = SignalType.booking_abandoned

    def emit(self, ctx: SessionCtx) -> list[Event]:
        s = ctx.pick_search()
        step = ctx.rng.choice([BookingStep.review, BookingStep.payment])
        return [
            ctx.event(SignalType.search_no_convert, s),
            ctx.event(SignalType.rate_view_no_progress, s),
            ctx.event(self.signal, s, step=step),
        ]


class ErrorHit:
    signal = SignalType.error_hit

    def emit(self, ctx: SessionCtx) -> list[Event]:
        s = ctx.pick_search()
        code = ctx.rng.choice(["VALIDATION_DATES", "PAYMENT_DECLINED", "SEARCH_TIMEOUT"])
        return [ctx.event(self.signal, s, error_code=code, step=BookingStep.payment)]


class ExtendedDwell:
    signal = SignalType.extended_dwell

    def emit(self, ctx: SessionCtx) -> list[Event]:
        s = ctx.pick_search()
        dwell = ctx.rng.randint(60_000, 240_000)  # > 60s threshold
        return [ctx.event(self.signal, s, step=BookingStep.select_vehicle, dwell_ms=dwell)]


class SessionEndedNoBooking:
    signal = SignalType.session_ended_no_booking

    def emit(self, ctx: SessionCtx) -> list[Event]:
        s = ctx.pick_search()
        return [
            ctx.event(SignalType.search_no_convert, s),
            ctx.event(self.signal, None),
        ]


class RepeatedSearch:
    """Deferred (I): same search across two sessions, no booking."""

    signal = SignalType.repeated_search

    def emit(self, ctx: SessionCtx) -> list[Event]:
        s = ctx.pick_search()
        e1 = ctx.event(SignalType.search_no_convert, s)
        # second session, days later, identical search
        ctx.clock.advance(timedelta(days=ctx.rng.randint(1, 5)))
        ctx.session_id = ctx.session_id + "-b"
        e2 = ctx.event(self.signal, s)
        return [e1, e2]


class Dormant:
    """Deferred (J): a derived dormancy candidate at login."""

    signal = SignalType.dormant

    def emit(self, ctx: SessionCtx) -> list[Event]:
        return [ctx.event(self.signal, None)]


PATTERNS: dict[SignalType, SignalPattern] = {
    p.signal: p
    for p in [
        SearchNoConvert(),
        RateViewNoProgress(),
        BookingAbandoned(),
        ErrorHit(),
        ExtendedDwell(),
        SessionEndedNoBooking(),
        RepeatedSearch(),
        Dormant(),
    ]
}
