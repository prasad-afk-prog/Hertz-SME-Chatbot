"""S6 — future-client-compatibility layer (POA/16 §16 item 6).

What these prove:

  * `GeneratedRepository` agrees with the world on **every** rate and
    availability key, not a spot-check — the repository is a seam, not a second
    source of truth, and drift here would silently break claim verification;
  * a completely different implementation satisfies the same protocol, so a
    real client feed can be swapped in without touching callers;
  * the lenient DTO renames, maps values and drops unmappable fields — and
    **reports** every one of those, because a silently dropped field is data
    loss;
  * the strict contract models are STILL strict after S6 lands. Leniency is a
    boundary step, not a relaxation of `extra="forbid"`;
  * `field_map.yaml` is coherent — every canonical field exists and every
    value-map target is a real enum member, so a YAML typo fails here rather
    than on live client data.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from generator.models import (
    Availability,
    Event,
    FuelType,
    Location,
    LocationType,
    RateCard,
    Transmission,
    VehicleCategory,
    VehicleClass,
)
from generator.repository import (
    DEFAULT_FIELD_MAP,
    FieldMap,
    GeneratedRepository,
    ReferenceRepository,
    StrictCoercionError,
    coerce,
    coerce_many,
    validate_field_map,
)


@pytest.fixture(scope="module")
def repo(world) -> GeneratedRepository:
    return GeneratedRepository(world)


@pytest.fixture(scope="module")
def field_map() -> FieldMap:
    return FieldMap.load(DEFAULT_FIELD_MAP)


# --- the seam ---------------------------------------------------------------- #
def test_generated_repository_satisfies_the_protocol(repo):
    assert isinstance(repo, ReferenceRepository)


def test_repository_agrees_with_the_world_on_every_rate(repo, world):
    """Every key, not a sample. The repository must be a pass-through — if it
    ever drifted from the world, a 'wrong price' test would stop being airtight."""
    assert world.rate_cards
    for rc in world.rate_cards:
        assert repo.rate(rc.location_id, rc.vehicle_class, rc.date) == rc.daily_rate


def test_repository_agrees_with_the_world_on_every_availability_key(repo, world):
    assert world.availability
    for a in world.availability:
        assert repo.availability_count(a.location_id, a.vehicle_class, a.date) == a.available
        assert repo.has(a.location_id, a.vehicle_class, a.date)


def test_repository_collections_are_the_same_objects(repo, world):
    assert repo.locations is world.locations
    assert repo.vehicle_classes is world.vehicle_classes
    assert repo.protection_products is world.protection_products
    assert repo.extras is world.extras
    assert repo.policies is world.policies


def test_repository_scalars_match(repo, world):
    assert repo.start == world.start
    assert repo.end == world.end
    assert repo.days == world.days
    assert repo.location_ids == world.location_ids
    assert repo.vehicle_codes == world.vehicle_codes
    for loc in world.location_ids:
        assert repo.currency(loc) == world.currency(loc)
    for vc in world.vehicle_codes:
        assert repo.deposit(vc) == world.deposit(vc)


def test_an_unrelated_implementation_also_satisfies_the_protocol():
    """The actual point of S6: something that is not the synthetic world can be
    dropped in. This stub stands in for a future client-backed repository."""

    class StubRepository:
        _rate = Decimal("99.00")

        @property
        def locations(self): return []
        @property
        def vehicle_classes(self): return []
        @property
        def rate_cards(self): return []
        @property
        def availability(self): return []
        @property
        def protection_products(self): return []
        @property
        def extras(self): return []
        @property
        def policies(self): return []
        @property
        def location_ids(self): return ["XXX"]
        @property
        def vehicle_codes(self): return ["ZCAR"]
        @property
        def start(self): return date(2026, 1, 1)
        @property
        def end(self): return date(2026, 1, 2)
        @property
        def days(self): return 2

        def rate(self, location_id, vehicle_class, on): return self._rate
        def availability_count(self, location_id, vehicle_class, on): return 1
        def has(self, location_id, vehicle_class, on): return True
        def currency(self, location_id): return "GBP"
        def deposit(self, vehicle_class): return Decimal("100.00")
        def nominal_daily_rate(self, location_id, vehicle_class): return self._rate

    stub: ReferenceRepository = StubRepository()
    assert isinstance(stub, ReferenceRepository)
    assert stub.rate("XXX", "ZCAR", date(2026, 1, 1)) == Decimal("99.00")


# --- the field map is coherent ---------------------------------------------- #
def test_shipped_field_map_loads_and_is_valid(field_map):
    problems = validate_field_map(field_map)
    assert problems == [], f"field_map.yaml is incoherent: {problems}"


def test_field_map_covers_the_reference_models(field_map):
    assert set(field_map.models) >= {"Location", "VehicleClass", "RateCard", "Availability"}


def test_validate_catches_an_unknown_canonical_field():
    fm = FieldMap.load(DEFAULT_FIELD_MAP)
    fm.models["Location"].fields["someClientField"] = "not_a_real_field"
    problems = validate_field_map(fm)
    assert any("not_a_real_field" in p for p in problems)


def test_validate_catches_a_typod_enum_target():
    """The footgun this check exists for: a value-map target that is not a real
    enum member fails silently at runtime otherwise."""
    fm = FieldMap.load(DEFAULT_FIELD_MAP)
    fm.models["VehicleClass"].values["transmission"]["SEMI"] = "semiautomatic"
    problems = validate_field_map(fm)
    assert any("semiautomatic" in p for p in problems)


def test_validate_catches_an_unknown_model():
    fm = FieldMap.load(DEFAULT_FIELD_MAP)
    fm.models["NotAModel"] = fm.models["Location"]
    assert any("NotAModel" in p for p in validate_field_map(fm))


# --- lenient coercion -------------------------------------------------------- #
def test_client_record_becomes_a_canonical_model(field_map):
    raw = {
        "stationCode": "BHX",
        "stationName": "Birmingham Airport",
        "countryCode": "GB",
        "regionName": "UK",
        "tz": "Europe/London",
        "stationType": "AIRPORT",
        "currencyCode": "GBP",
    }
    loc, report = coerce(Location, raw, field_map)
    assert isinstance(loc, Location)
    assert loc.location_id == "BHX"
    assert loc.type is LocationType.airport      # value-mapped from "AIRPORT"
    assert report.renamed["stationCode"] == "location_id"
    assert report.mapped_values["type"] == ("AIRPORT", "airport")
    assert report.lossless


def test_value_maps_normalise_client_vocabulary(field_map):
    raw = {
        "classCode": "XVAR",
        "classLabel": "Panel Van",
        "sampleModel": "Ford Transit",
        "vehicleCategory": "LCV",       # client word for a van
        "gearbox": "AUTO",              # client word for automatic
        "fuel": "EV",                   # client word for electric
    }
    vc, report = coerce(VehicleClass, raw, field_map)
    assert vc.category is VehicleCategory.van
    assert vc.transmission is Transmission.automatic
    assert vc.fuel_type is FuelType.electric
    assert len(report.mapped_values) == 3


def test_configured_drops_are_not_reported_as_loss(field_map):
    raw = {
        "stationCode": "LHR",
        "stationName": "London Heathrow",
        "countryCode": "GB",
        "regionName": "UK",
        "tz": "Europe/London",
        "internalStationRef": "ZZ-99",     # listed under drop:
        "lastSyncedAt": "2026-09-01",      # listed under drop:
    }
    loc, report = coerce(Location, raw, field_map)
    assert loc.location_id == "LHR"
    assert set(report.dropped_by_config) == {"internalStationRef", "lastSyncedAt"}
    assert report.lossless, "explicitly dropped fields are not data loss"


def test_unmapped_fields_are_dropped_AND_reported(field_map):
    """The guarantee that makes leniency safe: nothing disappears quietly."""
    raw = {
        "stationCode": "MAN",
        "stationName": "Manchester",
        "countryCode": "GB",
        "regionName": "UK",
        "tz": "Europe/London",
        "loyaltyPartnerCode": "AVIOS",     # neither mapped nor dropped
    }
    loc, report = coerce(Location, raw, field_map)
    assert loc.location_id == "MAN"
    assert report.dropped == {"loyaltyPartnerCode": "AVIOS"}
    assert not report.lossless


def test_strict_mode_refuses_to_lose_a_field(field_map):
    raw = {
        "stationCode": "MAN",
        "stationName": "Manchester",
        "countryCode": "GB",
        "regionName": "UK",
        "tz": "Europe/London",
        "loyaltyPartnerCode": "AVIOS",
    }
    with pytest.raises(StrictCoercionError, match="loyaltyPartnerCode"):
        coerce(Location, raw, field_map, strict=True)


def test_missing_required_field_still_fails_validation(field_map):
    """Leniency covers vocabulary, not completeness — a record missing a
    required field is broken data and must not be waved through."""
    with pytest.raises(ValidationError):
        coerce(Location, {"stationCode": "LHR"}, field_map)


def test_canonical_records_pass_through_unchanged(field_map):
    """A record already in canonical shape needs no map — important, because it
    means the layer costs nothing for our own generated data."""
    raw = {
        "location_id": "LHR", "name": "London Heathrow", "country": "GB",
        "region": "UK", "timezone": "Europe/London",
    }
    loc, report = coerce(Location, raw, field_map)
    assert loc.location_id == "LHR"
    assert report.renamed == {} and report.dropped == {}
    assert report.lossless


def test_coerce_many_reports_per_row(field_map):
    rows = [
        {"stationCode": "LHR", "stationName": "London Heathrow", "countryCode": "GB",
         "regionName": "UK", "tz": "Europe/London"},
        {"stationCode": "MAN", "stationName": "Manchester", "countryCode": "GB",
         "regionName": "UK", "tz": "Europe/London", "unexpected": 1},
    ]
    models, reports = coerce_many(Location, rows, field_map)
    assert [m.location_id for m in models] == ["LHR", "MAN"]
    assert reports[0].lossless and not reports[1].lossless


def test_rate_card_and_availability_coerce(field_map):
    rc, _ = coerce(RateCard, {
        "stationCode": "LHR", "classCode": "ICAR", "rateDate": "2026-09-01",
        "dailyNet": "48.50", "currencyCode": "GBP", "rateSource": "PRICING-SVC",
    }, field_map)
    assert rc.daily_rate == Decimal("48.50") and rc.date == date(2026, 9, 1)

    av, _ = coerce(Availability, {
        "stationCode": "LHR", "classCode": "ICAR", "onDate": "2026-09-01",
        "unitsFree": 7, "snapshotTakenAt": "2026-09-01T06:00:00Z",
    }, field_map)
    assert av.available == 7


# --- S6 did NOT relax the contract models ------------------------------------ #
def test_strict_models_still_reject_extra_fields():
    """test_contracts.py proves malformed input is rejected; this pins that S6
    left that guarantee intact. Coercion is a boundary step, not a loosening."""
    with pytest.raises(ValidationError):
        Location(
            location_id="LHR", name="London Heathrow", country="GB",
            region="UK", timezone="Europe/London", stationCode="LHR",
        )
    with pytest.raises(ValidationError):
        Event(
            event_id="e1", customer_id="c1", session_id="s1",
            signal_type="search_no_convert", occurred_at="2026-09-01T10:00:00Z",
            surprise=True,
        )


def test_coerce_without_a_map_is_still_lenient_about_unknowns():
    """No map supplied: canonical fields pass, unknowns are dropped and
    reported. A caller with no client mapping yet still gets a usable path."""
    loc, report = coerce(Location, {
        "location_id": "LHR", "name": "London Heathrow", "country": "GB",
        "region": "UK", "timezone": "Europe/London", "junk": "x",
    })
    assert loc.location_id == "LHR"
    assert report.dropped == {"junk": "x"}
