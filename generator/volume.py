"""VolumeSampler — Tier B bulk data from documented distributions (POA/16 §6/§7).
Assertions on this tier are aggregate/invariant, not per-record.
"""
from __future__ import annotations

from .config import GenConfig
from .customers import CustomerFactory
from .models import Booking, Customer, Event, Session
from .sessions import SessionSimulator
from .world import World


class VolumeSampler:
    def __init__(self, cfg: GenConfig, world: World) -> None:
        self.cfg = cfg
        self.world = world

    def build(self) -> tuple[list[Customer], list[Booking], list[Session], list[Event]]:
        customers, bookings = CustomerFactory(self.cfg, self.world).build()
        sessions, events = SessionSimulator(self.cfg, self.world).build(customers)
        return customers, bookings, sessions, events
