"""Shared resilience primitives — circuit breaker, TTL cache, injected clock.

Extracted from M10 when M09 needed the same circuit breaker (POA/09 §3.4,
POA/10 §3.2). Two independent breaker implementations would drift, and one of
them would eventually be the one that is wrong — the same reasoning that keeps
the claim-rewrite policy in `generator.reference` rather than copied into the
verification service.

**Time is always injected.** Every timeout, TTL and cooldown takes a `clock`
callable so tests assert expiry rather than sleeping for it. The suite runs in
well under a second and must stay that way; a wall-clock test here would be both
slow and flaky.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Hashable

Clock = Callable[[], float]


@dataclass
class CircuitBreaker:
    """Closed -> (failures reach threshold) -> Open -> (cooldown) -> Half-open.

    Half-open lets exactly one probe through: success closes it, failure reopens
    for another cooldown. While open, calls fail immediately rather than each
    paying the full timeout.

    Only *dependency* failures should be recorded. A bad lookup against a
    healthy service is not a reason to open the circuit — see M10's handling of
    `NoDataForKey`.
    """
    threshold: int = 3
    cooldown_s: float = 30.0
    clock: Clock = time.monotonic

    _consecutive_failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if self.clock() - self._opened_at >= self.cooldown_s:
            return False                      # cooled down -> half-open
        return True

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "open" if self.is_open else "half-open"

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.threshold:
            self._opened_at = self.clock()


@dataclass
class TTLCache:
    """Short-TTL cache on an injected clock.

    Only successful results belong here. Caching a failure means an outage keeps
    poisoning callers for the rest of the TTL, even after the dependency
    recovers and the breaker closes.
    """
    ttl_s: float = 30.0
    clock: Clock = time.monotonic
    _entries: dict[Hashable, tuple[float, object]] = field(default_factory=dict, init=False)

    def get(self, key: Hashable) -> object | None:
        hit = self._entries.get(key)
        if hit is None:
            return None
        stored_at, value = hit
        if self.clock() - stored_at >= self.ttl_s:
            del self._entries[key]
            return None
        return value

    def put(self, key: Hashable, value: object) -> None:
        self._entries[key] = (self.clock(), value)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
