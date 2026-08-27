"""CustomerFactory — customers + booking history consistent with the world.

`last_booking_at` is set so a configurable share of customers are past the
dormancy threshold (drives signal J).

v0.2 — customers are linked to their business Company and RatePlan, and bookings
carry a real lifecycle (upcoming / active / completed / cancelled), a reference
number, chosen extras & protection, a named driver, a derived (not flat) total,
deposit, VAT and a cancellation policy — so the chatbot can answer booking,
pricing and modify/cancel queries.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .catalogues import plan_for
from .config import GenConfig
from .models import (
    Booking,
    BookingDriver,
    BookingStatus,
    Cancellation,
    Company,
    Consent,
    Customer,
    CustomerType,
    RatePlan,
    Segment,
)
from .rng import sub_rng, weighted_choice
from .world import World

_REGION_LANG = {
    "UK/en": ("UK", "en"),
    "DE/de": ("DE", "de"),
    "FR/fr": ("FR", "fr"),
    "ES/es": ("ES", "es"),
}
_SEGMENTS = [Segment.new, Segment.occasional, Segment.frequent, Segment.dormant]
_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class CustomerFactory:
    def __init__(
        self,
        cfg: GenConfig,
        world: World,
        companies: list[Company] | None = None,
        rate_plans: list[RatePlan] | None = None,
    ) -> None:
        self.cfg = cfg
        self.world = world
        self.rng = sub_rng(cfg.seed, "customers")
        self.now = datetime(2026, 8, 24, 9, 0, 0, tzinfo=timezone.utc)
        # business links
        self.companies_by_type: dict[CustomerType, list[Company]] = {}
        for c in companies or []:
            self.companies_by_type.setdefault(c.customer_type, []).append(c)
        self.discount = {p.rate_plan_id: (p.discount_pct or Decimal("0")) for p in (rate_plans or [])}
        # catalogue price lookups
        self._extra = {e.code: e for e in world.extras}
        self._prot = {p.code: p for p in world.protection_products}

    def build(self, n: int | None = None) -> tuple[list[Customer], list[Booking]]:
        n = n or self.cfg.n_customers
        customers: list[Customer] = []
        bookings: list[Booking] = []
        for i in range(n):
            cust, bks = self._one(i)
            customers.append(cust)
            bookings.extend(bks)
        return customers, bookings

    # --- helpers -------------------------------------------------------- #
    def _reference_no(self) -> str:
        return "HZ" + "".join(self.rng.choices(_REF_ALPHABET, k=8))

    def _pick_extras(self) -> list[str]:
        picks = []
        if self.rng.random() < 0.40 and "ADD_DRIVER" in self._extra:
            picks.append("ADD_DRIVER")
        if self.rng.random() < 0.25 and "GPS" in self._extra:
            picks.append("GPS")
        if self.rng.random() < 0.15 and "CHILD_SEAT" in self._extra:
            picks.append("CHILD_SEAT")
        return picks

    def _pick_protection(self) -> list[str]:
        picks = []
        if self.rng.random() < 0.50 and "SUPERCOVER" in self._prot:
            picks.append("SUPERCOVER")
        if self.rng.random() < 0.20 and "PAI" in self._prot:
            picks.append("PAI")
        return picks

    def _value(
        self, loc: str, vc: str, rental_days: int, discount: Decimal,
        extras: list[str], protection: list[str],
    ) -> tuple[Decimal, Decimal]:
        """Return (gross_total, vat) derived from the world — never a flat rate."""
        net_daily = (self.world.nominal_daily_rate(loc, vc) * (Decimal("1") - discount)).quantize(Decimal("0.01"))
        gross = net_daily * Decimal(rental_days)
        for code in extras:
            e = self._extra.get(code)
            if not e:
                continue
            gross += e.price * Decimal(rental_days) if e.pricing_unit.value == "per_day" else e.price
        for code in protection:
            p = self._prot.get(code)
            if p:
                gross += p.daily_price * Decimal(rental_days)
        gross = gross.quantize(Decimal("0.01"))
        vat = (gross - (gross / Decimal("1.20"))).quantize(Decimal("0.01"))
        return gross, vat

    def _booking(
        self, cid: str, company_id: str | None, discount: Decimal, idx: str,
        loc: str, vc: str, pickup_at: datetime, return_at: datetime, status: BookingStatus,
    ) -> Booking:
        rental_days = (return_at - pickup_at).days or 1
        extras = self._pick_extras()
        protection = self._pick_protection()
        total, vat = self._value(loc, vc, rental_days, discount, extras, protection)
        min_age = 21
        for v in self.world.vehicle_classes:
            if v.code == vc and v.min_driver_age:
                min_age = v.min_driver_age
        driver = BookingDriver(
            age=self.rng.randint(min_age, 65),
            licence_held_years=self.rng.randint(1, 30),
        )
        cancel = Cancellation(
            fee=(total / Decimal(rental_days)).quantize(Decimal("0.01")),
            deadline=pickup_at - timedelta(hours=48),
            policy="Free cancellation up to 48 hours before pickup; then one rental day is charged.",
        )
        return Booking(
            booking_id=f"bk-{idx}",
            customer_id=cid,
            company_id=company_id,
            reference_no=self._reference_no(),
            pickup=loc,
            dropoff=loc,
            vehicle_class=vc,
            pickup_at=pickup_at,
            return_at=return_at,
            total=total,
            tax=vat,
            currency=self.world.currency(loc),
            status=status,
            extras=extras,
            protection=protection,
            driver=driver,
            deposit=self.world.deposit(vc),
            cancellation=cancel,
        )

    # --- one customer --------------------------------------------------- #
    def _one(self, i: int) -> tuple[Customer, list[Booking]]:
        cid = f"hfb-cust-{i:06d}"
        ctype = weighted_choice(self.rng, self.cfg.customer_type_mix)
        region_key = weighted_choice(self.rng, self.cfg.region_language)
        region, language = _REGION_LANG[region_key]
        segment = self.rng.choice(_SEGMENTS)
        created = self.now - timedelta(days=self.rng.randint(30, 900))

        # business account + negotiated plan
        company_id: str | None = None
        plan_id = plan_for(ctype)
        if ctype != CustomerType.individual:
            pool = self.companies_by_type.get(ctype, [])
            if pool:
                company = self.rng.choice(pool)
                company_id = company.company_id
                plan_id = company.rate_plan_id or plan_id
        discount = self.discount.get(plan_id or "", Decimal("0"))

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
            status = BookingStatus.cancelled if self.rng.random() < 0.06 else BookingStatus.completed
            bks.append(self._booking(cid, company_id, discount, f"{i:06d}-{b}", loc, vc, pickup_at, return_at, status))
            if status != BookingStatus.cancelled and (last_booking_at is None or pickup_at > last_booking_at):
                last_booking_at = pickup_at

        # a currently-active rental (small share of active segments)
        if segment in (Segment.frequent, Segment.occasional) and self.rng.random() < 0.06:
            loc = self.rng.choice(self.world.location_ids)
            vc = self.rng.choice(self.world.vehicle_codes)
            pickup_at = self.now - timedelta(days=1)
            return_at = self.now + timedelta(days=self.rng.randint(2, 5))
            bks.append(self._booking(cid, company_id, discount, f"{i:06d}-a", loc, vc, pickup_at, return_at, BookingStatus.active))

        # an upcoming (future, manageable) booking within the world's rate window
        if segment in (Segment.new, Segment.occasional, Segment.frequent) and self.rng.random() < 0.35:
            loc = self.rng.choice(self.world.location_ids)
            vc = self.rng.choice(self.world.vehicle_codes)
            offset = self.rng.randint(0, max(0, self.world.days - 6))
            pickup_day = self.world.start + timedelta(days=offset)
            pickup_at = datetime(pickup_day.year, pickup_day.month, pickup_day.day, 10, 0, tzinfo=timezone.utc)
            return_at = pickup_at + timedelta(days=self.rng.randint(1, 5))
            bks.append(self._booking(cid, company_id, discount, f"{i:06d}-u", loc, vc, pickup_at, return_at, BookingStatus.upcoming))

        # ensure some customers are dormant (past threshold) for signal J
        if segment == Segment.dormant and last_booking_at is not None:
            last_booking_at = self.now - timedelta(days=self.cfg.dormancy_days + self.rng.randint(5, 120))

        cust = Customer(
            customer_id=cid,
            customer_type=ctype,
            region=region,
            language=language,
            segment=segment,
            created_at=created,
            consent=Consent(marketing=self.rng.random() > 0.1, analytics=self.rng.random() > 0.05),
            negotiated_rate_plan=plan_id,
            last_booking_at=last_booking_at,
            company_id=company_id,
            rate_plan_id=plan_id,
        )
        return cust, bks
