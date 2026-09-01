"""S1 (POA/16 §16.2) — the expanded 12-class taxonomy, city/suburban/region
stations and one-way support, and proof the golden prices are unchanged.
"""
from __future__ import annotations

# The 12-class Hertz-style taxonomy (ACRISS-style codes) mandated by §16.2.
TAXONOMY = {"ECAR", "CCAR", "ICAR", "FCAR", "PCAR", "LCAR",
            "CFAR", "IFAR", "SFAR", "LFAR", "IVAR", "PPAR"}


def test_twelve_class_taxonomy_present(world):
    codes = set(world.vehicle_codes)
    missing = TAXONOMY - codes
    assert not missing, f"missing taxonomy classes: {missing}"


def test_station_types_cover_airport_city_suburban(world):
    types = {loc.type.value for loc in world.locations}
    assert {"airport", "city", "suburban"} <= types, f"only have station types {types}"


def test_multiple_countries_and_currencies(world):
    countries = {loc.country for loc in world.locations}
    assert len(countries) >= 4, f"expected several regions, got {countries}"
    assert "US" in countries, "expected a US (new-region) station"
    currencies = {loc.currency for loc in world.locations}
    assert {"GBP", "EUR", "USD"} <= currencies


def test_rate_and_availability_exist_for_every_key(world):
    """Every station × class combo (incl. the new ones) is priced and stocked."""
    for loc in world.location_ids:
        for vc in world.vehicle_codes:
            assert world.has(loc, vc, world.start), f"no rate/avail for {loc}/{vc}"
            assert world.rate(loc, vc, world.start) > 0


def test_one_way_is_domestic_only(world):
    assert world.allows_one_way("LHR", "MAN")          # both GB
    assert not world.allows_one_way("LHR", "FRA")      # GB -> DE is cross-border
    assert not world.allows_one_way("LHR", "LHR")      # same station is not one-way
    dests = world.one_way_destinations("JFK")
    assert "NYC" in dests                              # US domestic one-way
    assert all(world.allows_one_way("JFK", d) for d in dests)


def test_one_way_fee_positive_for_every_class(world):
    for vc in world.vehicle_codes:
        assert world.one_way_fee("LHR", vc) > 0


def test_golden_prices_unchanged(world):
    """The v0.2 golden-scenario anchors must be byte-stable after the expansion
    (the additive pass-3 design keeps passes 1 & 2 untouched)."""
    assert str(world.rate("LHR", "ICAR", world.start)) == "52.21"
    assert str(world.rate("MAN", "ECAR", world.start)) == "31.47"
    assert str(world.rate("FRA", "FCAR", world.start)) == "60.63"
