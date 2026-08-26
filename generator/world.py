"""WorldBuilder — the seeded reference world that backs BOTH the generated
events/claims and the booking-API mock (design principle P2). This is what makes
claim-verification tests airtight.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from .models import Availability, Location, RateCard, VehicleClass
from .rng import sub_rng

# Fixed, small, realistic-enough reference set.
_LOCATIONS = [
    Location(location_id="LHR", name="London Heathrow", country="GB", region="UK", timezone="Europe/London"),
    Location(location_id="MAN", name="Manchester Airport", country="GB", region="UK", timezone="Europe/London"),
    Location(location_id="EDI", name="Edinburgh Airport", country="GB", region="UK", timezone="Europe/London"),
    Location(location_id="FRA", name="Frankfurt Airport", country="DE", region="DE", timezone="Europe/Berlin"),
    Location(location_id="CDG", name="Paris Charles de Gaulle", country="FR", region="FR", timezone="Europe/Paris"),
    Location(location_id="MAD", name="Madrid Barajas", country="ES", region="ES", timezone="Europe/Madrid"),
]

_VEHICLE_CLASSES = [
    VehicleClass(code="ECAR", label="Economy", example_model="VW Polo"),
    VehicleClass(code="CCAR", label="Compact", example_model="Ford Focus"),
    VehicleClass(code="ICAR", label="Intermediate", example_model="VW Golf"),
    VehicleClass(code="FCAR", label="Fullsize", example_model="Skoda Octavia"),
    VehicleClass(code="SUV", label="SUV", example_model="Nissan Qashqai"),
]

# base daily rate per class (GBP)
_BASE_RATE = {"ECAR": 32, "CCAR": 38, "ICAR": 46, "FCAR": 55, "SUV": 72}
# location multiplier
_LOC_MULT = {"LHR": 1.15, "MAN": 1.0, "EDI": 1.05, "FRA": 1.1, "CDG": 1.12, "MAD": 0.95}


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
    ) -> None:
        self.locations = locations
        self.vehicle_classes = vehicle_classes
        self.rate_cards = rate_cards
        self.availability = availability
        self.start = start
        self.end = end
        self.days = (end - start).days + 1
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


class WorldBuilder:
    def __init__(self, seed: int, start: date | None = None, days: int = 90) -> None:
        self.rng = sub_rng(seed, "world")
        self.start = start or date(2026, 9, 1)
        self.end = self.start + timedelta(days=days - 1)
        self.days = days

    def build(self) -> World:
        rate_cards: list[RateCard] = []
        availability: list[Availability] = []
        for i in range(self.days):
            d = self.start + timedelta(days=i)
            weekend = d.weekday() >= 5
            for loc in _LOCATIONS:
                for vc in _VEHICLE_CLASSES:
                    base = Decimal(_BASE_RATE[vc.code]) * Decimal(str(_LOC_MULT[loc.location_id]))
                    if weekend:
                        base *= Decimal("1.15")
                    # small deterministic jitter (+/- 3%) from the seeded rng
                    jitter = Decimal(str(1 + (self.rng.random() - 0.5) * 0.06))
                    rate = (base * jitter).quantize(Decimal("0.01"))
                    rate_cards.append(
                        RateCard(location_id=loc.location_id, vehicle_class=vc.code, date=d, daily_rate=rate)
                    )
                    availability.append(
                        Availability(
                            location_id=loc.location_id,
                            vehicle_class=vc.code,
                            date=d,
                            available=self.rng.randint(0, 12),
                        )
                    )
        return World(_LOCATIONS, _VEHICLE_CLASSES, rate_cards, availability, self.start, self.end)
