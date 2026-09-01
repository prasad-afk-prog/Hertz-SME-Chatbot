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
        return count <= self.limit
