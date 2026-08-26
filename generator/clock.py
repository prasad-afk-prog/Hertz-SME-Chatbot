"""Injectable deterministic clock (design principle P6: time is data).

The system-under-test must take time from an injected clock, never from
datetime.now(), so deferred / expiry / dormant scenarios run in milliseconds.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class Clock:
    def __init__(self, start: datetime | None = None) -> None:
        # default: a fixed instant so runs are reproducible
        self._now = start or datetime(2026, 8, 24, 9, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def tick(self, seconds: float = 1.0) -> datetime:
        self._now += timedelta(seconds=seconds)
        return self._now

    def advance(self, delta: timedelta) -> datetime:
        self._now += delta
        return self._now

    def fork(self) -> "Clock":
        """A copy at the current instant (independent timeline)."""
        return Clock(self._now)
