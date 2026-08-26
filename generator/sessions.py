"""SessionSimulator — per customer, generate sessions whose events realise a
signal type drawn from the configured signal mix (Tier B volume driver).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .clock import Clock
from .config import GenConfig
from .models import Customer, Event, Session
from .patterns import PATTERNS, SessionCtx
from .rng import sub_rng, weighted_choice
from .world import World


class SessionSimulator:
    def __init__(self, cfg: GenConfig, world: World) -> None:
        self.cfg = cfg
        self.world = world
        self.rng = sub_rng(cfg.seed, "sessions")

    def build(self, customers: list[Customer]) -> tuple[list[Session], list[Event]]:
        sessions: list[Session] = []
        events: list[Event] = []
        base = datetime(2026, 8, 1, 8, 0, 0, tzinfo=timezone.utc)
        for c in customers:
            n_sessions = self.rng.randint(1, self.cfg.max_sessions_per_customer)
            for s in range(n_sessions):
                login = base + timedelta(days=self.rng.randint(0, 23), hours=self.rng.randint(0, 12))
                clock = Clock(login)
                sid = f"sess-{c.customer_id.split('-')[-1]}-{s:02d}"
                signal = weighted_choice(self.rng, self.cfg.signal_mix)
                ctx = SessionCtx(c.customer_id, sid, self.world, self.rng, clock)
                evs = PATTERNS[signal].emit(ctx)
                events.extend(evs)
                sessions.append(
                    Session(session_id=sid, customer_id=c.customer_id, login_at=login, logout_at=clock.now())
                )
        return sessions, events
