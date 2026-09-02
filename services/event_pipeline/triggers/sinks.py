"""Downstream sinks + config/idempotency seams for the trigger engine.

Node-N outputs are messages to sinks, not direct calls (POA/18 §5), so A5 stays
decoupled from M06/M08/M14: fire -> FireSink (M08/B2), deferred -> DeferredSink
(M06/A7), suppressed -> SuppressionSink (M14). Rules come from a RuleSource
(M13; hot-reload/caching is a prod extension), and an IdempotencyGuard makes
stream re-delivery safe (POA/04 §3.3).
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from generator.models import EngagementDecision, FireMessage, TriggerConfig


class DeferredItem(BaseModel):
    """A5 -> M06/A7 (Track-A internal): a deferred match to schedule."""
    model_config = ConfigDict(extra="forbid")
    trigger_id: str
    customer_id: str
    event_id: str
    wait_period: str = "PT0S"
    expiry: str = "P3D"
    occurred_at: datetime


# ---- sinks ------------------------------------------------------------- #
class FireSink(Protocol):
    def emit(self, message: FireMessage) -> None: ...


class DeferredSink(Protocol):
    def enqueue(self, item: DeferredItem) -> None: ...


class SuppressionSink(Protocol):
    def record(self, decision: EngagementDecision) -> None: ...


class InMemoryFireSink:
    def __init__(self) -> None:
        self.messages: list[FireMessage] = []

    def emit(self, message: FireMessage) -> None:
        self.messages.append(message)


class InMemoryDeferredSink:
    def __init__(self) -> None:
        self.items: list[DeferredItem] = []

    def enqueue(self, item: DeferredItem) -> None:
        self.items.append(item)


class InMemorySuppressionSink:
    def __init__(self) -> None:
        self.suppressions: list[EngagementDecision] = []

    def record(self, decision: EngagementDecision) -> None:
        self.suppressions.append(decision)


# ---- rule source (M13) + idempotency (POA/04 §3.3) -------------------- #
class RuleSource(Protocol):
    def active(self) -> list[TriggerConfig]: ...


class StaticRuleSource:
    def __init__(self, rules: list[TriggerConfig]) -> None:
        self._rules = list(rules)

    def active(self) -> list[TriggerConfig]:
        return self._rules


class IdempotencyGuard(Protocol):
    def seen(self, event_id: str) -> bool: ...     # True if already processed (and marks it)


class InMemoryIdempotencyGuard:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def seen(self, event_id: str) -> bool:
        if event_id in self._seen:
            return True
        self._seen.add(event_id)
        return False
