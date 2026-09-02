"""S2 (POA/16 §16.1) — itemised/disputable fees on bookings (late-return,
no-show, fuel, one-way) and the hand-authored fee-dispute fixtures.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from generator.config import GenConfig
from generator.durations import late_return_extra_days
from generator.models import BookingStatus, DisputeResolution, FeeType
from generator.scenarios import FeeDisputeComposer
from generator.volume import VolumeSampler


@pytest.fixture(scope="module")
def bookings(world):
    cfg = GenConfig(seed=42, n_customers=600)
    _c, bks, _s, _e = VolumeSampler(cfg, world).build()
    return bks


# --- generated fee data ------------------------------------------------- #
def test_no_show_bookings_exist(bookings):
    assert any(b.status == BookingStatus.no_show for b in bookings)


def test_one_way_bookings_exist_and_are_priced(bookings):
    one_way = [b for b in bookings if b.dropoff != b.pickup]
    assert one_way, "expected some one-way bookings"
    for b in one_way:
        assert b.one_way_fee and b.one_way_fee > 0
        assert any(f.code == FeeType.one_way for f in b.fees)


def test_all_fee_types_are_exercised(bookings):
    seen = {f.code for b in bookings for f in b.fees}
    for code in (FeeType.one_way, FeeType.late_return, FeeType.no_show, FeeType.fuel):
        assert code in seen, f"no {code.value} fee generated in the volume tier"


def test_no_show_fee_only_appears_on_no_show_bookings(bookings):
    for b in bookings:
        if any(f.code == FeeType.no_show for f in b.fees):
            assert b.status == BookingStatus.no_show


def test_some_fees_are_disputed_and_explained(bookings):
    disputed = [f for b in bookings for f in b.fees if f.disputed]
    assert disputed, "expected some disputed charges to drive 'why was I charged X?'"
    assert all(f.dispute_reason for f in disputed), "a disputed line must say why"


def test_one_way_fee_field_matches_a_fee_line(bookings):
    for b in bookings:
        if b.one_way_fee is not None:
            lines = [f for f in b.fees if f.code == FeeType.one_way]
            assert lines and lines[0].amount == b.one_way_fee


# --- late-return duration rule ------------------------------------------ #
def test_late_return_grace_and_day_counting():
    assert late_return_extra_days(timedelta(0)) == 0
    assert late_return_extra_days(timedelta(minutes=20)) == 0       # within grace
    assert late_return_extra_days(timedelta(minutes=40)) == 1       # past grace -> 1 day
    assert late_return_extra_days(timedelta(hours=3)) == 1
    assert late_return_extra_days(timedelta(hours=26)) == 2


# --- hand-authored dispute fixtures ------------------------------------- #
def test_dispute_fixtures_are_internally_consistent(world):
    disputes = FeeDisputeComposer(world).all()
    assert len(disputes) == 5
    assert len({d.dispute_id for d in disputes}) == 5

    resolutions = {d.resolution for d in disputes}
    assert {DisputeResolution.upheld, DisputeResolution.refunded,
            DisputeResolution.partial_refund, DisputeResolution.escalated_to_human} <= resolutions

    for d in disputes:
        amt = d.fee.amount
        assert d.fee.disputed and d.customer_message and d.grounds
        if d.resolution == DisputeResolution.upheld:
            assert d.correct_amount == amt
        elif d.resolution == DisputeResolution.refunded:
            assert d.correct_amount == Decimal("0.00")
        elif d.resolution == DisputeResolution.partial_refund:
            assert Decimal("0.00") < d.correct_amount < amt
        elif d.resolution == DisputeResolution.escalated_to_human:
            # the fee itself is valid; only the unverifiable claim is escalated
            assert d.correct_amount == amt
