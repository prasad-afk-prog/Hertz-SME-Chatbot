"""VolumeSampler — Tier B bulk data from documented distributions (POA/16 §6/§7).
Assertions on this tier are aggregate/invariant, not per-record.

v0.2 — also builds the business layer (rate plans, companies, invoices). To keep
the existing test contract, `build()` still returns the 4-tuple
(customers, bookings, sessions, events); the business objects are exposed as
attributes (`rate_plans`, `companies`, `invoices`) after `build()`.
"""
from __future__ import annotations

from .business import CompanyFactory, build_invoices
from .catalogues import rate_plans as _rate_plans
from .config import GenConfig
from .customers import CustomerFactory
from .models import Booking, Company, Customer, Event, Invoice, RatePlan, Session
from .sessions import SessionSimulator
from .world import World


class VolumeSampler:
    def __init__(self, cfg: GenConfig, world: World) -> None:
        self.cfg = cfg
        self.world = world
        self.rate_plans: list[RatePlan] = []
        self.companies: list[Company] = []
        self.invoices: list[Invoice] = []

    def build(self) -> tuple[list[Customer], list[Booking], list[Session], list[Event]]:
        self.rate_plans = _rate_plans()
        self.companies = CompanyFactory(self.cfg, self.rate_plans).build()
        customers, bookings = CustomerFactory(self.cfg, self.world, self.companies, self.rate_plans).build()
        self.invoices = build_invoices(self.companies, bookings)
        sessions, events = SessionSimulator(self.cfg, self.world).build(customers)
        return customers, bookings, sessions, events
