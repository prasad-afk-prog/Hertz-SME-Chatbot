"""CustomerFactory — customers + booking history consistent with the world.

`last_booking_at` is set so a configurable share of customers are past the
dormancy threshold (drives signal J).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .config import GenConfig
from .models import Booking, Consent, Customer, CustomerType, Segment
from .rng import sub_rng, weighted_choice
from .world import World

_REGION_LANG = {
    "UK/en": ("UK", "en"),
    "DE/de": ("DE", "de"),
    "FR/fr": ("FR", "fr"),
    "ES/es": ("ES", "es"),
}
_SEGMENTS = [Segment.new, Segment.occasional, Segment.frequent, Segment.dormant]


class CustomerFactory:
    def __init__(self, cfg: GenConfig, world: World) -> None:
        self.cfg = cfg
        self.world = world
        self.rng = sub_rng(cfg.seed, "customers")
        self.now = datetime(2026, 8, 24, 9, 0, 0, tzinfo=timezone.utc)

    def build(self, n: int | None = None) -> tuple[list[Customer], list[Booking]]:
        n = n or self.cfg.n_customers
        customers: list[Customer] = []
        bookings: list[Booking] = []
        for i in range(n):
            cust, bks = self._one(i)
            customers.append(cust)
            bookings.extend(bks)
        return customers, bookings

    def _one(self, i: int) -> tuple[Customer, list[Booking]]:
        cid = f"hfb-cust-{i:06d}"
        ctype = weighted_choice(self.rng, self.cfg.customer_type_mix)
        region_key = weighted_choice(self.rng, self.cfg.region_language)
        region, language = _REGION_LANG[region_key]
        segment = self.rng.choice(_SEGMENTS)
        created = self.now - timedelta(days=self.rng.randint(30, 900))

        # past bookings (0-4); dormant/new customers skew to fewer/older
        n_bookings = 0 if segment == Segment.new else self.rng.randint(0, 4)
        bks: list[Booking] = []
        last_booking_at: datetime | None = None
        for b in range(n_bookings):
            loc = self.rng.choice(self.world.location_ids)
            vc = self.rng.choice(self.world.vehicle_codes)
            days_ago = self.rng.randint(10, 400)
            pickup_at = self.now - timedelta(days=days_ago)
            return_at = pickup_at + timedelta(days=self.rng.randint(1, 7))
            rental_days = (return_at - pickup_at).days or 1
            # value them off a plausible historic rate
            total = (Decimal(rental_days) * Decimal("45.00")).quantize(Decimal("0.01"))
            bks.append(
                Booking(
                    booking_id=f"bk-{i:06d}-{b}",
                    customer_id=cid,
                    pickup=loc,
                    dropoff=loc,
                    vehicle_class=vc,
                    pickup_at=pickup_at,
                    return_at=return_at,
                    total=total,
                )
            )
            if last_booking_at is None or pickup_at > last_booking_at:
                last_booking_at = pickup_at

        # ensure some customers are dormant (past threshold) for signal J
        if segment == Segment.dormant and last_booking_at is not None:
            last_booking_at = self.now - timedelta(days=self.cfg.dormancy_days + self.rng.randint(5, 120))

        plan = f"{ctype.value}-STD-2026" if ctype != CustomerType.individual else None
        cust = Customer(
            customer_id=cid,
            customer_type=ctype,
            region=region,
            language=language,
            segment=segment,
            created_at=created,
            consent=Consent(marketing=self.rng.random() > 0.1, analytics=self.rng.random() > 0.05),
            negotiated_rate_plan=plan,
            last_booking_at=last_booking_at,
        )
        return cust, bks
