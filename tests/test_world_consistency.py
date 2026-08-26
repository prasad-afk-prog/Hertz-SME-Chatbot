"""The world is internally consistent and deterministic across seeds (P2, P4)."""
from __future__ import annotations

from generator.world import WorldBuilder


def test_rate_and_availability_exist_for_every_key(world):
    for loc in world.location_ids:
        for vc in world.vehicle_codes:
            assert world.has(loc, vc, world.start)
            assert world.rate(loc, vc, world.start) > 0
            assert world.availability_count(loc, vc, world.start) >= 0


def test_world_is_deterministic_for_same_seed():
    a = WorldBuilder(42).build()
    b = WorldBuilder(42).build()
    assert a.rate("LHR", "ICAR", a.start) == b.rate("LHR", "ICAR", b.start)
    assert len(a.rate_cards) == len(b.rate_cards)


def test_world_differs_for_different_seed():
    a = WorldBuilder(1).build()
    b = WorldBuilder(2).build()
    # jitter is seed-dependent, so at least some rates differ
    diffs = sum(
        1
        for vc in a.vehicle_codes
        if a.rate("LHR", vc, a.start) != b.rate("LHR", vc, b.start)
    )
    assert diffs > 0


def test_some_zero_availability_exists(world):
    """The 'availability wrong' scenario needs at least one sold-out key."""
    zeros = [a for a in world.availability if a.date == world.start and a.available == 0]
    assert zeros, "expected at least one 0-availability key on day 0"
