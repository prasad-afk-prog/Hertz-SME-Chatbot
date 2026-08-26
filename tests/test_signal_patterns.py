"""Each of the 8 signal patterns reproduces its signal, grounded in the world,
including the deferred ones (repeated_search across sessions, dormant customer).
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from generator.clock import Clock
from generator.config import GenConfig
from generator.customers import CustomerFactory
from generator.models import Segment, SignalType
from generator.patterns import PATTERNS, SessionCtx
from generator.rng import sub_rng


@pytest.mark.parametrize("signal", list(SignalType))
def test_pattern_emits_its_signal(world, signal):
    ctx = SessionCtx("hfb-cust-t", "sess-t-00", world, sub_rng(1, str(signal)), Clock())
    events = PATTERNS[signal].emit(ctx)
    assert events, f"{signal} emitted no events"
    # the pattern's characteristic signal is present
    assert any(e.signal_type == signal for e in events)
    # every event is grounded in the world (or intentionally context-free)
    for e in events:
        if e.context.pickup is not None:
            assert e.context.pickup in world.location_ids
            assert e.context.vehicle_class in world.vehicle_codes
            assert world.has(e.context.pickup, e.context.vehicle_class, e.context.pickup_at.date())


def test_repeated_search_spans_two_sessions(world):
    ctx = SessionCtx("hfb-cust-r", "sess-r-00", world, sub_rng(2, "rep"), Clock())
    events = PATTERNS[SignalType.repeated_search].emit(ctx)
    assert len({e.session_id for e in events}) == 2
    # identical search across both
    assert events[0].context.pickup == events[1].context.pickup
    assert events[0].context.pickup_at == events[1].context.pickup_at


def test_extended_dwell_exceeds_threshold(world):
    ctx = SessionCtx("hfb-cust-d", "sess-d-00", world, sub_rng(3, "dwell"), Clock())
    (event,) = PATTERNS[SignalType.extended_dwell].emit(ctx)
    assert event.context.dwell_ms is not None and event.context.dwell_ms > 60_000


def test_error_hit_carries_error_code(world):
    ctx = SessionCtx("hfb-cust-e", "sess-e-00", world, sub_rng(4, "err"), Clock())
    (event,) = PATTERNS[SignalType.error_hit].emit(ctx)
    assert event.context.error_code is not None


def test_some_customers_are_dormant(world):
    """Signal J requires customers whose last booking predates the dormancy window."""
    cfg = GenConfig(seed=42, n_customers=300)
    factory = CustomerFactory(cfg, world)
    customers, _ = factory.build()

    dormant_segment = [c for c in customers if c.segment == Segment.dormant]
    past_threshold = [
        c
        for c in customers
        if c.last_booking_at is not None
        and (factory.now - c.last_booking_at) > timedelta(days=cfg.dormancy_days)
    ]
    assert dormant_segment, "expected some dormant-segment customers"
    assert past_threshold, "expected some customers past the dormancy threshold"
