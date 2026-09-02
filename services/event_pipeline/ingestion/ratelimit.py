"""Rate limiting / backpressure (POA/02 §2, §5.7).

A ``RateLimiter`` protocol with an in-memory fixed-window default. Single-process
only — production uses a Redis-backed counter for distributed limiting (POA/02
§8); the seam keeps that swap a one-line change.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol


class RateLimiter(Protocol):
    def allow(self, source: str, customer_id: str) -> bool: ...


class NoRateLimit:
    def allow(self, source: str, customer_id: str) -> bool:
        return True


class InMemoryRateLimiter:
    """Per-(source, customer) fixed-window limiter."""

    def __init__(self, limit_per_min: int, now: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit_per_min
        self._now = now
        self._windows: dict[str, tuple[float, int]] = {}

    #: Drop windows this many seconds past their start. Without eviction the
    #: dict keeps one entry per (source, customer) seen since boot — a slow leak
    #: in a long-lived process, and this one is on the live request path.
    _EVICT_AFTER_S = 300.0

    def allow(self, source: str, customer_id: str) -> bool:
        if self.limit <= 0:
            return True
        key = f"{source}:{customer_id}"
        now = self._now()
        start, count = self._windows.get(key, (now, 0))
        if now - start >= 60.0:
            start, count = now, 0
        count += 1
        self._windows[key] = (start, count)

        if len(self._windows) > 1024:
            self._evict(now)
        return count <= self.limit

    def _evict(self, now: float) -> None:
        stale = [k for k, (start, _) in self._windows.items() if now - start >= self._EVICT_AFTER_S]
        for key in stale:
            del self._windows[key]
