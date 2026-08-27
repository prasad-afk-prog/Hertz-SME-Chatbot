"""Business layer (v0.2): companies and invoices.

Companies are the SME/corporate accounts that business customers belong to; each
sits on a negotiated RatePlan. Invoices roll up a company's completed bookings
so the chatbot can answer account/billing queries. All seeded and deterministic.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .catalogues import plan_for
from .config import GenConfig
from .models import (
    Booking,
    BookingStatus,
    Company,
    Contact,
    CustomerType,
    Invoice,
    InvoiceLine,
    RatePlan,
)
from .rng import sub_rng, weighted_choice

_NAME_A = ["Northgate", "Kingsway", "Riverside", "Pennine", "Cavendish", "Ashworth",
           "Broadoak", "Sterling", "Meridian", "Clearwater", "Whitfield", "Oakline"]
_NAME_B = ["Logistics", "Consulting", "Engineering", "Media", "Facilities", "Recruitment",
           "Contracts", "Distribution", "Surveying", "Interiors", "Analytics", "Trading"]
_SUFFIX = ["Ltd", "Group", "Services", "LLP"]
_FIRST = ["Alex", "Priya", "Tom", "Sofia", "James", "Aisha", "Daniel", "Emma", "Raj", "Laura"]
_LAST = ["Bennett", "Kaur", "O'Connor", "Nowak", "Fischer", "Martin", "Reeves", "Osei", "Lopez", "Hall"]
_CREDIT = ["14 days", "30 days", "45 days", "60 days"]
_TIERS = ["Silver", "Gold", "Platinum"]


class CompanyFactory:
    def __init__(self, cfg: GenConfig, rate_plans: list[RatePlan] | None = None) -> None:
        self.cfg = cfg
        self.rng = sub_rng(cfg.seed, "companies")
        self.plan_ids = {p.rate_plan_id for p in (rate_plans or [])}
        self.type_mix = {CustomerType.SME: 0.7, CustomerType.corporate: 0.3}

    def build(self, n: int | None = None) -> list[Company]:
        n = n if n is not None else max(5, self.cfg.n_customers // 20)
        companies: list[Company] = []
        for i in range(n):
            ctype = weighted_choice(self.rng, self.type_mix)
            name = f"{self.rng.choice(_NAME_A)} {self.rng.choice(_NAME_B)} {self.rng.choice(_SUFFIX)}"
            plan = plan_for(ctype)
            plan = plan if (not self.plan_ids or plan in self.plan_ids) else None
            first, last = self.rng.choice(_FIRST), self.rng.choice(_LAST)
            slug = name.split()[0].lower()
            n_cc = self.rng.randint(1, 3)
            cost_centres = [f"CC-{self.rng.randint(100, 899)}" for _ in range(n_cc)]
            companies.append(
                Company(
                    company_id=f"co-{i:04d}",
                    name=name,
                    customer_type=ctype,
                    account_no=f"{ctype.value}-{self.rng.randint(100000, 999999)}",
                    cost_centres=sorted(set(cost_centres)),
                    credit_terms=self.rng.choice(_CREDIT),
                    rate_plan_id=plan,
                    primary_contact=Contact(
                        name=f"{first} {last}",
                        email=f"{first.lower()}.{last.lower().replace(chr(39), '')}@{slug}.example",
                        phone=f"+44 20 7{self.rng.randint(100, 999)} {self.rng.randint(1000, 9999)}",
                    ),
                    loyalty_tier=self.rng.choice(_TIERS),
                )
            )
        return companies


def build_invoices(companies: list[Company], bookings: list[Booking]) -> list[Invoice]:
    """One rolled-up invoice per company covering its completed bookings."""
    by_company: dict[str, list[Booking]] = defaultdict(list)
    for b in bookings:
        if b.company_id and b.status == BookingStatus.completed:
            by_company[b.company_id].append(b)

    invoices: list[Invoice] = []
    for company in companies:
        bks = by_company.get(company.company_id, [])
        if not bks:
            continue
        lines: list[InvoiceLine] = []
        net = vat = gross = Decimal("0.00")
        for b in bks:
            g = b.total
            v = b.tax if b.tax is not None else (g - (g / Decimal("1.20"))).quantize(Decimal("0.01"))
            n = (g - v).quantize(Decimal("0.01"))
            lines.append(
                InvoiceLine(
                    booking_id=b.booking_id,
                    description=f"{b.vehicle_class} rental {b.pickup}→{b.dropoff} "
                                f"({b.pickup_at.date()} → {b.return_at.date()})",
                    net=n, vat=v, gross=g,
                )
            )
            net += n; vat += v; gross += g
        invoices.append(
            Invoice(
                invoice_id=f"inv-{company.company_id}-2026Q2",
                company_id=company.company_id,
                period="2026-Q2",
                line_items=lines,
                net=net.quantize(Decimal("0.01")),
                vat=vat.quantize(Decimal("0.01")),
                gross=gross.quantize(Decimal("0.01")),
                currency="GBP",
                po_number=f"PO-{company.account_no.split('-')[-1]}",
                status="issued",
            )
        )
    return invoices
