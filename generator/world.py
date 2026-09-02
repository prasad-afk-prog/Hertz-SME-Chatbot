"""WorldBuilder — the seeded reference world that backs BOTH the generated
events/claims and the booking-API mock (design principle P2). This is what makes
claim-verification tests airtight.

v0.2 — locations and vehicle classes carry real business attributes, van classes
are added, currency is per-location, and the world also holds the static
catalogues (protection / extras / policies). Van rate-cards are generated in a
SECOND pass so the original car rate/availability RNG sequence is unchanged and
the golden-scenario prices stay stable.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from .catalogues import extras as _extras
from .catalogues import policies as _policies
from .catalogues import protection_products as _protection
from .models import (
    Availability,
    Extra,
    FuelType,
    Location,
    LocationType,
    Policy,
    ProtectionProduct,
    RateCard,
    Transmission,
    VehicleCategory,
    VehicleClass,
)
from .rng import sub_rng

# Fixed, small, realistic-enough reference set.
_LOCATIONS = [
    Location(location_id="LHR", name="London Heathrow", country="GB", region="UK", timezone="Europe/London",
             type=LocationType.airport, currency="GBP",
             address="Northern Perimeter Rd, Hounslow TW6 2QD", opening_hours="Mon-Sun 06:00-23:00"),
    Location(location_id="MAN", name="Manchester Airport", country="GB", region="UK", timezone="Europe/London",
             type=LocationType.airport, currency="GBP",
             address="Terminal 1, Manchester M90 1QX", opening_hours="Mon-Sun 07:00-23:00"),
    Location(location_id="EDI", name="Edinburgh Airport", country="GB", region="UK", timezone="Europe/London",
             type=LocationType.airport, currency="GBP",
             address="Edinburgh Airport, Ingliston EH12 9DN", opening_hours="Mon-Sun 07:00-23:00"),
    Location(location_id="FRA", name="Frankfurt Airport", country="DE", region="DE", timezone="Europe/Berlin",
             type=LocationType.airport, currency="EUR",
             address="60547 Frankfurt am Main", opening_hours="Mon-Sun 06:00-23:00"),
    Location(location_id="CDG", name="Paris Charles de Gaulle", country="FR", region="FR", timezone="Europe/Paris",
             type=LocationType.airport, currency="EUR",
             address="95700 Roissy-en-France", opening_hours="Mon-Sun 06:00-23:30"),
    Location(location_id="MAD", name="Madrid Barajas", country="ES", region="ES", timezone="Europe/Madrid",
             type=LocationType.airport, currency="EUR",
             address="Av. de la Hispanidad, 28042 Madrid", opening_hours="Mon-Sun 07:00-23:00"),
]

# S1 (POA/16 §16.2) — additive city / suburban / new-region stations. Kept in a
# SEPARATE list so build() passes 1 & 2 iterate the ORIGINAL six only and the
# car/van RNG sequence (hence the golden-scenario prices) is unchanged.
_EXTRA_LOCATIONS = [
    Location(location_id="LON", name="London City (Marble Arch)", country="GB", region="UK",
             timezone="Europe/London", type=LocationType.city, currency="GBP",
             address="Marble Arch, London W1H 7EJ", opening_hours="Mon-Sun 07:00-20:00"),
    Location(location_id="CRY", name="London Croydon", country="GB", region="UK",
             timezone="Europe/London", type=LocationType.suburban, currency="GBP",
             address="Purley Way, Croydon CR0 4RE", opening_hours="Mon-Sat 08:00-18:00"),
    Location(location_id="PARC", name="Paris Gare de Lyon", country="FR", region="FR",
             timezone="Europe/Paris", type=LocationType.city, currency="EUR",
             address="Place Louis Armand, 75012 Paris", opening_hours="Mon-Sun 07:00-21:00"),
    Location(location_id="MADS", name="Madrid Alcalá de Henares", country="ES", region="ES",
             timezone="Europe/Madrid", type=LocationType.suburban, currency="EUR",
             address="Av. de Madrid 10, 28802 Alcalá de Henares", opening_hours="Mon-Sat 08:00-19:00"),
    Location(location_id="JFK", name="New York JFK", country="US", region="US",
             timezone="America/New_York", type=LocationType.airport, currency="USD",
             address="JFK Airport, Queens, NY 11430", opening_hours="Mon-Sun 06:00-23:00"),
    Location(location_id="NYC", name="New York Midtown", country="US", region="US",
             timezone="America/New_York", type=LocationType.city, currency="USD",
             address="W 43rd St, New York, NY 10036", opening_hours="Mon-Sun 07:00-20:00"),
]
_ALL_LOCATIONS = _LOCATIONS + _EXTRA_LOCATIONS

_CAR_CLASSES = [
    VehicleClass(code="ECAR", label="Economy", example_model="VW Polo", category=VehicleCategory.car,
                 seats=5, doors=3, transmission=Transmission.manual, fuel_type=FuelType.petrol,
                 luggage=1, deposit=Decimal("150.00"), min_driver_age=21, mileage_policy="unlimited"),
    VehicleClass(code="CCAR", label="Compact", example_model="Ford Focus", category=VehicleCategory.car,
                 seats=5, doors=5, transmission=Transmission.manual, fuel_type=FuelType.petrol,
                 luggage=2, deposit=Decimal("175.00"), min_driver_age=21, mileage_policy="unlimited"),
    VehicleClass(code="ICAR", label="Intermediate", example_model="VW Golf", category=VehicleCategory.car,
                 seats=5, doors=5, transmission=Transmission.manual, fuel_type=FuelType.diesel,
                 luggage=2, deposit=Decimal("200.00"), min_driver_age=23, mileage_policy="unlimited"),
    VehicleClass(code="FCAR", label="Fullsize", example_model="Skoda Octavia", category=VehicleCategory.car,
                 seats=5, doors=5, transmission=Transmission.automatic, fuel_type=FuelType.diesel,
                 luggage=3, deposit=Decimal("250.00"), min_driver_age=23, mileage_policy="unlimited"),
    VehicleClass(code="SUV", label="SUV", example_model="Nissan Qashqai", category=VehicleCategory.car,
                 seats=5, doors=5, transmission=Transmission.automatic, fuel_type=FuelType.diesel,
                 luggage=3, deposit=Decimal("300.00"), min_driver_age=25, mileage_policy="unlimited"),
]

_VAN_CLASSES = [
    VehicleClass(code="PVAN", label="Panel Van", example_model="Ford Transit Custom", category=VehicleCategory.van,
                 seats=3, doors=4, transmission=Transmission.manual, fuel_type=FuelType.diesel,
                 luggage=0, deposit=Decimal("400.00"), min_driver_age=25, mileage_policy="limited-250mi/day"),
    VehicleClass(code="LVAN", label="Luton Van", example_model="Ford Transit Luton", category=VehicleCategory.van,
                 seats=3, doors=3, transmission=Transmission.manual, fuel_type=FuelType.diesel,
                 luggage=0, deposit=Decimal("500.00"), min_driver_age=25, mileage_policy="limited-250mi/day"),
]

# S1 (POA/16 §16.2) — the rest of the 12-class Hertz-style taxonomy, mapped to
# ACRISS-style codes. Additive: emitted only in build() pass 3, so the original
# car/van RNG sequence and the golden-scenario prices are unchanged. (The legacy
# generic "SUV" code is retained for backward-compat with existing golden
# scenarios/fixtures; with these four it becomes the coarse SUV tier.)
_EXTRA_CLASSES = [
    VehicleClass(code="PCAR", label="Premium", example_model="BMW 5 Series", category=VehicleCategory.car,
                 seats=5, doors=5, transmission=Transmission.automatic, fuel_type=FuelType.diesel,
                 luggage=3, deposit=Decimal("350.00"), min_driver_age=25, mileage_policy="unlimited"),
    VehicleClass(code="LCAR", label="Luxury", example_model="Mercedes-Benz E-Class", category=VehicleCategory.car,
                 seats=5, doors=5, transmission=Transmission.automatic, fuel_type=FuelType.petrol,
                 luggage=3, deposit=Decimal("500.00"), min_driver_age=30, mileage_policy="unlimited"),
    VehicleClass(code="CFAR", label="Compact SUV", example_model="Nissan Juke", category=VehicleCategory.car,
                 seats=5, doors=5, transmission=Transmission.automatic, fuel_type=FuelType.petrol,
                 luggage=2, deposit=Decimal("300.00"), min_driver_age=23, mileage_policy="unlimited"),
    VehicleClass(code="IFAR", label="Midsize SUV", example_model="Volkswagen Tiguan", category=VehicleCategory.car,
                 seats=5, doors=5, transmission=Transmission.automatic, fuel_type=FuelType.diesel,
                 luggage=3, deposit=Decimal("350.00"), min_driver_age=25, mileage_policy="unlimited"),
    VehicleClass(code="SFAR", label="Full-size SUV", example_model="Audi Q7", category=VehicleCategory.car,
                 seats=7, doors=5, transmission=Transmission.automatic, fuel_type=FuelType.diesel,
                 luggage=4, deposit=Decimal("450.00"), min_driver_age=25, mileage_policy="unlimited"),
    VehicleClass(code="LFAR", label="Special/Luxury SUV", example_model="Range Rover", category=VehicleCategory.car,
                 seats=5, doors=5, transmission=Transmission.automatic, fuel_type=FuelType.diesel,
                 luggage=4, deposit=Decimal("750.00"), min_driver_age=30, mileage_policy="unlimited"),
    VehicleClass(code="IVAR", label="Minivan", example_model="Ford Galaxy", category=VehicleCategory.car,
                 seats=7, doors=5, transmission=Transmission.automatic, fuel_type=FuelType.diesel,
                 luggage=4, deposit=Decimal("350.00"), min_driver_age=25, mileage_policy="unlimited"),
    VehicleClass(code="PPAR", label="Pickup Truck", example_model="Ford Ranger", category=VehicleCategory.car,
                 seats=5, doors=4, transmission=Transmission.automatic, fuel_type=FuelType.diesel,
                 luggage=2, deposit=Decimal("400.00"), min_driver_age=25, mileage_policy="limited-250mi/day"),
]

_V02_CLASSES = _CAR_CLASSES + _VAN_CLASSES              # what passes 1 & 2 emit (unchanged)
_VEHICLE_CLASSES = _V02_CLASSES + _EXTRA_CLASSES        # the full published taxonomy

# base daily rate per class (GBP-equivalent list price; per-location multiplier applies)
_BASE_RATE = {"ECAR": 32, "CCAR": 38, "ICAR": 46, "FCAR": 55, "SUV": 72, "PVAN": 65, "LVAN": 85,
              "PCAR": 70, "LCAR": 110, "CFAR": 60, "IFAR": 75, "SFAR": 95, "LFAR": 140,
              "IVAR": 80, "PPAR": 78}
# location multiplier (new city stations pricier, suburban cheaper, US premium)
_LOC_MULT = {"LHR": 1.15, "MAN": 1.0, "EDI": 1.05, "FRA": 1.1, "CDG": 1.12, "MAD": 0.95,
             "LON": 1.10, "CRY": 0.90, "PARC": 1.08, "MADS": 0.88, "JFK": 1.20, "NYC": 1.25}
# one-way drop fee per class (GBP)
_ONE_WAY_FEE = {"ECAR": 45, "CCAR": 45, "ICAR": 55, "FCAR": 65, "SUV": 75, "PVAN": 95, "LVAN": 120,
                "PCAR": 75, "LCAR": 120, "CFAR": 70, "IFAR": 80, "SFAR": 95, "LFAR": 140,
                "IVAR": 95, "PPAR": 90}
# deposit per class (mirrors the VehicleClass attribute, for O(1) lookup)
_DEPOSIT = {v.code: v.deposit for v in _VEHICLE_CLASSES}
_CURRENCY = {loc.location_id: loc.currency for loc in _ALL_LOCATIONS}
# country per station — one-way is domestic-only (cross-border needs approval, see policies)
_COUNTRY = {loc.location_id: loc.country for loc in _ALL_LOCATIONS}


class World:
    """Holds the reference world + O(1) lookups used by the booking-API mock."""

    def __init__(
        self,
        locations: list[Location],
        vehicle_classes: list[VehicleClass],
        rate_cards: list[RateCard],
        availability: list[Availability],
        start: date,
        end: date,
        protection_products: list[ProtectionProduct] | None = None,
        extras: list[Extra] | None = None,
        policies: list[Policy] | None = None,
    ) -> None:
        self.locations = locations
        self.vehicle_classes = vehicle_classes
        self.rate_cards = rate_cards
        self.availability = availability
        self.start = start
        self.end = end
        self.days = (end - start).days + 1
        self.protection_products = protection_products or []
        self.extras = extras or []
        self.policies = policies or []
        self._rate = {(r.location_id, r.vehicle_class, r.date): r.daily_rate for r in rate_cards}
        self._avail = {(a.location_id, a.vehicle_class, a.date): a.available for a in availability}

    @property
    def location_ids(self) -> list[str]:
        return [l.location_id for l in self.locations]

    @property
    def vehicle_codes(self) -> list[str]:
        return [v.code for v in self.vehicle_classes]

    def rate(self, location_id: str, vehicle_class: str, on: date) -> Decimal:
        return self._rate[(location_id, vehicle_class, on)]

    def availability_count(self, location_id: str, vehicle_class: str, on: date) -> int:
        return self._avail[(location_id, vehicle_class, on)]

    def has(self, location_id: str, vehicle_class: str, on: date) -> bool:
        return (location_id, vehicle_class, on) in self._rate

    def currency(self, location_id: str) -> str:
        return _CURRENCY.get(location_id, "GBP")

    def deposit(self, vehicle_class: str) -> Decimal:
        return _DEPOSIT.get(vehicle_class) or Decimal("200.00")

    def nominal_daily_rate(self, location_id: str, vehicle_class: str) -> Decimal:
        """A date-independent list price — used to value historic/out-of-window
        bookings whose exact rate-card day is not in the generated window."""
        base = Decimal(_BASE_RATE.get(vehicle_class, 45)) * Decimal(str(_LOC_MULT.get(location_id, 1.0)))
        return base.quantize(Decimal("0.01"))

    # --- one-way (S1 — POA/16 §16.2) ------------------------------------ #
    def one_way_fee(self, location_id: str, vehicle_class: str) -> Decimal:
        """The nominal one-way drop fee for dropping a class off away from its
        pickup station. Date-independent list value (mirrors RateCard.one_way_fee)."""
        return Decimal(_ONE_WAY_FEE.get(vehicle_class, 50)).quantize(Decimal("0.01"))

    def allows_one_way(self, pickup: str, dropoff: str) -> bool:
        """One-way is offered domestically; cross-border needs prior approval
        (see the cross-border policy), so it's excluded from generated bookings."""
        if pickup == dropoff:
            return False
        return _COUNTRY.get(pickup) is not None and _COUNTRY.get(pickup) == _COUNTRY.get(dropoff)

    def one_way_destinations(self, pickup: str) -> list[str]:
        """Valid drop-off stations for a one-way rental picked up at `pickup`."""
        return [lid for lid in self.location_ids if self.allows_one_way(pickup, lid)]


class WorldBuilder:
    def __init__(self, seed: int, start: date | None = None, days: int = 90) -> None:
        self.rng = sub_rng(seed, "world")
        self.start = start or date(2026, 9, 1)
        self.end = self.start + timedelta(days=days - 1)
        self.days = days

    def _rate_card(self, loc: Location, vc: VehicleClass, d: date, weekend: bool) -> tuple[RateCard, Availability]:
        base = Decimal(_BASE_RATE[vc.code]) * Decimal(str(_LOC_MULT[loc.location_id]))
        if weekend:
            base *= Decimal("1.15")
        # small deterministic jitter (+/- 3%) from the seeded rng
        jitter = Decimal(str(1 + (self.rng.random() - 0.5) * 0.06))
        rate = (base * jitter).quantize(Decimal("0.01"))
        avail = self.rng.randint(0, 12)
        card = RateCard(
            location_id=loc.location_id, vehicle_class=vc.code, date=d, daily_rate=rate,
            currency=loc.currency,
            weekly_rate=(rate * Decimal("6")).quantize(Decimal("0.01")),   # 7 days for the price of 6
            deposit=_DEPOSIT[vc.code],
            tax_rate=Decimal("0.20"),
            one_way_fee=Decimal(_ONE_WAY_FEE[vc.code]).quantize(Decimal("0.01")),
        )
        availability = Availability(location_id=loc.location_id, vehicle_class=vc.code, date=d, available=avail)
        return card, availability

    def build(self) -> World:
        rate_cards: list[RateCard] = []
        availability: list[Availability] = []
        # Pass 1: cars (unchanged RNG sequence -> stable golden-scenario prices).
        for i in range(self.days):
            d = self.start + timedelta(days=i)
            weekend = d.weekday() >= 5
            for loc in _LOCATIONS:
                for vc in _CAR_CLASSES:
                    card, av = self._rate_card(loc, vc, d, weekend)
                    rate_cards.append(card)
                    availability.append(av)
        # Pass 2: vans (purely additive).
        for i in range(self.days):
            d = self.start + timedelta(days=i)
            weekend = d.weekday() >= 5
            for loc in _LOCATIONS:
                for vc in _VAN_CLASSES:
                    card, av = self._rate_card(loc, vc, d, weekend)
                    rate_cards.append(card)
                    availability.append(av)
        # Pass 3 (S1 — POA/16 §16.2): additive — the new taxonomy classes at every
        # station, and every class at the new city/suburban/region stations. Runs
        # AFTER passes 1 & 2 so their RNG draws (and the golden prices) are
        # untouched; `covered` is exactly the (station, class) pairs those emitted.
        covered = {(loc.location_id, vc.code) for loc in _LOCATIONS for vc in _V02_CLASSES}
        for i in range(self.days):
            d = self.start + timedelta(days=i)
            weekend = d.weekday() >= 5
            for loc in _ALL_LOCATIONS:
                for vc in _VEHICLE_CLASSES:
                    if (loc.location_id, vc.code) in covered:
                        continue
                    card, av = self._rate_card(loc, vc, d, weekend)
                    rate_cards.append(card)
                    availability.append(av)
        return World(
            _ALL_LOCATIONS, _VEHICLE_CLASSES, rate_cards, availability, self.start, self.end,
            protection_products=_protection(), extras=_extras(), policies=_policies(),
        )
