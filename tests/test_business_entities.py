"""v0.2 business layer: companies, rate plans, invoices, catalogues and the
enriched booking lifecycle are schema-valid and referentially consistent.

Built in-memory from the seeded world (same style as the other tiers) so the
tests don't depend on regenerated files on disk.
"""
from __future__ import annotations

from collections import Counter

import pytest

from generator.config import GenConfig
from generator.models import BookingStatus, CustomerType
from generator.volume import VolumeSampler


@pytest.fixture(scope="module")
def sampled(world):
    cfg = GenConfig(seed=42, n_customers=400)
    sampler = VolumeSampler(cfg, world)
    customers, bookings, _sessions, _events = sampler.build()
    return {
        "customers": customers,
        "bookings": bookings,
        "companies": sampler.companies,
        "rate_plans": sampler.rate_plans,
        "invoices": sampler.invoices,
    }


def test_catalogues_present_and_unique(world):
    assert world.protection_products and world.extras and world.policies
    for cat in (world.protection_products, world.extras):
        codes = [x.code for x in cat]
        assert len(codes) == len(set(codes)), "catalogue codes must be unique"


def test_van_classes_exist(world):
    vans = [v for v in world.vehicle_classes if v.category.value == "van"]
    assert vans, "expected at least one van/LCV class"
    for v in vans:
        assert v.deposit and v.min_driver_age and v.mileage_policy


def test_per_location_currency(world):
    # Currency is a function of country and consistent within each country
    # (S1 added city/suburban stations + a US/USD region — POA/16 §16.2).
    expected = {"GB": "GBP", "DE": "EUR", "FR": "EUR", "ES": "EUR", "IE": "EUR", "US": "USD"}
    by_country: dict[str, set] = {}
    for loc in world.locations:
        assert loc.country in expected, f"no currency rule for country {loc.country!r}"
        assert loc.currency == expected[loc.country], (
            f"{loc.location_id} ({loc.country}) should price in "
            f"{expected[loc.country]}, got {loc.currency}"
        )
        by_country.setdefault(loc.country, set()).add(loc.currency)
    for country, currencies in by_country.items():
        assert len(currencies) == 1, f"{country} has inconsistent currencies {currencies}"


def test_business_customers_link_to_a_real_plan(sampled):
    plan_ids = {p.rate_plan_id for p in sampled["rate_plans"]}
    for c in sampled["customers"]:
        if c.customer_type != CustomerType.individual:
            assert c.rate_plan_id in plan_ids, f"{c.customer_id} has an unknown rate plan"
        else:
            assert c.company_id is None


def test_customer_and_booking_company_refs_resolve(sampled):
    company_ids = {co.company_id for co in sampled["companies"]}
    for c in sampled["customers"]:
        if c.company_id is not None:
            assert c.company_id in company_ids
    for b in sampled["bookings"]:
        if b.company_id is not None:
            assert b.company_id in company_ids


def test_booking_extras_and_protection_codes_are_valid(world, sampled):
    extra_codes = {e.code for e in world.extras}
    prot_codes = {p.code for p in world.protection_products}
    for b in sampled["bookings"]:
        assert set(b.extras) <= extra_codes
        assert set(b.protection) <= prot_codes


def test_booking_lifecycle_is_exercised(sampled):
    counts = Counter(b.status for b in sampled["bookings"])
    for status in (BookingStatus.completed, BookingStatus.upcoming, BookingStatus.cancelled):
        assert counts[status] > 0, f"no {status.value} bookings generated"


def test_booking_totals_are_derived_not_flat(sampled):
    totals = {b.total for b in sampled["bookings"]}
    assert len(totals) > 20, "totals look flat/synthetic, not derived from the rate world"


def test_upcoming_bookings_are_manageable(sampled):
    upcoming = [b for b in sampled["bookings"] if b.status == BookingStatus.upcoming]
    assert upcoming, "expected some upcoming bookings to manage"
    for b in upcoming:
        assert b.return_at > b.pickup_at
        assert b.cancellation is not None and b.reference_no


def test_invoices_reference_valid_companies_and_bookings(sampled):
    company_ids = {co.company_id for co in sampled["companies"]}
    booking_ids = {b.booking_id for b in sampled["bookings"]}
    for inv in sampled["invoices"]:
        assert inv.company_id in company_ids
        assert inv.gross == inv.net + inv.vat
        for line in inv.line_items:
            assert line.booking_id in booking_ids
