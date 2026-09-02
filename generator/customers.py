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

from .catalogues import fee_rules, plan_for
from .config import GenConfig
from .durations import late_return_extra_days
from .models import (
    Booking,
    BookingDriver,
    BookingStatus,
    Cancellation,
    Company,
    Consent,
    Customer,
    CustomerType,
    FeeLine,
    FeeType,
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
    "US/en": ("US", "en"),
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
        self.fees = fee_rules()   # S2 — post-rental fee rules (single source of truth)
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

    def _pick_dropoff(self, loc: str) -> str:
        """Sometimes make a booking one-way (dropoff != pickup, same country).
        (S2/S1 — POA/16 §16.2.)"""
        if self.rng.random() < self.cfg.one_way_booking_share:
            dests = self.world.one_way_destinations(loc)
            if dests:
                return self.rng.choice(dests)
        return loc

    def _daily_value(self, loc: str, vc: str, discount: Decimal) -> Decimal:
        """The net (post-discount) daily rate this booking is priced at."""
        return (self.world.nominal_daily_rate(loc, vc) * (Decimal("1") - discount)).quantize(Decimal("0.01"))

    def _value(
        self, loc: str, vc: str, rental_days: int, discount: Decimal,
        extras: list[str], protection: list[str], one_way_fee: Decimal = Decimal("0"),
    ) -> tuple[Decimal, Decimal]:
        """Return (gross_total, vat) derived from the world — never a flat rate.
        The one-way fee (if any) is part of the quoted total."""
        net_daily = self._daily_value(loc, vc, discount)
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
        gross += one_way_fee
        gross = gross.quantize(Decimal("0.01"))
        vat = (gross - (gross / Decimal("1.20"))).quantize(Decimal("0.01"))
        return gross, vat

    def _compose_fees(
        self, loc: str, vc: str, one_way: bool, ow_fee: Decimal | None,
        daily_value: Decimal, status: BookingStatus,
    ) -> list[FeeLine]:
        """Itemised charges on a booking (S2 — POA/16 §16.1). `one_way` is priced
        into the total; late-return / no-show / fuel are post-rental charges, a
        fraction of which are flagged disputed to drive 'why was I charged X?'."""
        currency = self.world.currency(loc)
        fees: list[FeeLine] = []
        if one_way and ow_fee is not None:
            fees.append(FeeLine(code=FeeType.one_way, label="One-way drop-off fee",
                                amount=ow_fee, currency=currency))
        if status == BookingStatus.no_show:
            amt = (daily_value * Decimal(self.fees["no_show_days_charged"])).quantize(Decimal("0.01"))
            disputed = self.rng.random() < 0.30
            fees.append(FeeLine(
                code=FeeType.no_show, label="No-show fee (one rental day)",
                amount=amt, currency=currency, disputed=disputed,
                dispute_reason="Customer says they cancelled before pickup" if disputed else None))
        elif status == BookingStatus.completed:
            if self.rng.random() < 0.15:                       # late return
                overdue = timedelta(hours=self.rng.randint(1, 30))
                days = late_return_extra_days(overdue, self.fees["late_return_grace_minutes"])
                if days > 0:
                    amt = (daily_value * Decimal(days)).quantize(Decimal("0.01"))
                    disputed = self.rng.random() < 0.25
                    fees.append(FeeLine(
                        code=FeeType.late_return,
                        label=f"Late return ({days} extra day{'s' if days > 1 else ''})",
                        amount=amt, currency=currency, disputed=disputed,
                        dispute_reason="Customer says they returned within the grace period" if disputed else None))
            if self.rng.random() < 0.12:                       # fuel charge
                missing = self.rng.randint(5, self.fees["tank_litres"])
                amt = (self.fees["fuel_service_charge"]
                       + Decimal(missing) * self.fees["fuel_price_per_litre"]).quantize(Decimal("0.01"))
                disputed = self.rng.random() < 0.20
                fees.append(FeeLine(
                    code=FeeType.fuel, label=f"Refuelling charge ({missing} L + service fee)",
                    amount=amt, currency=currency, disputed=disputed,
                    dispute_reason="Customer says they returned the tank full" if disputed else None))
        return fees

    def _booking(
        self, cid: str, company_id: str | None, discount: Decimal, idx: str,
        loc: str, vc: str, pickup_at: datetime, return_at: datetime, status: BookingStatus,
        dropoff: str | None = None,
    ) -> Booking:
        dropoff = dropoff or loc
        one_way = self.world.allows_one_way(loc, dropoff)
        ow_fee = self.world.one_way_fee(loc, vc) if one_way else None
        rental_days = (return_at - pickup_at).days or 1
        extras = self._pick_extras()
        protection = self._pick_protection()
        total, vat = self._value(loc, vc, rental_days, discount, extras, protection, ow_fee or Decimal("0"))
        daily_value = self._daily_value(loc, vc, discount)
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
        fees = self._compose_fees(loc, vc, one_way, ow_fee, daily_value, status)
        return Booking(
            booking_id=f"bk-{idx}",
            customer_id=cid,
            company_id=company_id,
            reference_no=self._reference_no(),
            pickup=loc,
            dropoff=dropoff,
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
            one_way_fee=ow_fee,
            fees=fees,
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
            r = self.rng.random()
            if r < 0.04:                       # S2 — no-show (never collected)
                status = BookingStatus.no_show
            elif r < 0.10:
                status = BookingStatus.cancelled
            else:
                status = BookingStatus.completed
            dropoff = self._pick_dropoff(loc)
            bks.append(self._booking(cid, company_id, discount, f"{i:06d}-{b}", loc, vc, pickup_at, return_at, status, dropoff=dropoff))
            if status == BookingStatus.completed and (last_booking_at is None or pickup_at > last_booking_at):
                last_booking_at = pickup_at

        # a currently-active rental (small share of active segments)
        if segment in (Segment.frequent, Segment.occasional) and self.rng.random() < 0.06:
            loc = self.rng.choice(self.world.location_ids)
            vc = self.rng.choice(self.world.vehicle_codes)
            pickup_at = self.now - timedelta(days=1)
            return_at = self.now + timedelta(days=self.rng.randint(2, 5))
            dropoff = self._pick_dropoff(loc)
            bks.append(self._booking(cid, company_id, discount, f"{i:06d}-a", loc, vc, pickup_at, return_at, BookingStatus.active, dropoff=dropoff))

        # an upcoming (future, manageable) booking within the world's rate window
        if segment in (Segment.new, Segment.occasional, Segment.frequent) and self.rng.random() < 0.35:
            loc = self.rng.choice(self.world.location_ids)
            vc = self.rng.choice(self.world.vehicle_codes)
            offset = self.rng.randint(0, max(0, self.world.days - 6))
            pickup_day = self.world.start + timedelta(days=offset)
            pickup_at = datetime(pickup_day.year, pickup_day.month, pickup_day.day, 10, 0, tzinfo=timezone.utc)
            return_at = pickup_at + timedelta(days=self.rng.randint(1, 5))
            dropoff = self._pick_dropoff(loc)
            bks.append(self._booking(cid, company_id, discount, f"{i:06d}-u", loc, vc, pickup_at, return_at, BookingStatus.upcoming, dropoff=dropoff))

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
