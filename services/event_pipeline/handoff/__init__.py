"""A8 Human Handoff Manager (POA/07 / M07).

    from services.event_pipeline.handoff import HandoffManager, MockQueueAdapter

Routes a HandoffRequest via M13 rules, packages context, dispatches into the
agent queue with retry + fallback + dead-letter, and records the lifecycle.
"""
from __future__ import annotations

from . import bootstrap
from .adapter import (
    DeadLetterSink,
    InMemoryDeadLetterSink,
    MockQueueAdapter,
    QueueAdapter,
)
from .ledger import HandoffLedger
from .manager import HandoffManager, HandoffResult, HandoffStatus
from .packager import package, summary_of
from .routing import context_of, route_for
from .tables import handoffs, metadata

__all__ = [
    "HandoffManager",
    "HandoffResult",
    "HandoffStatus",
    "HandoffLedger",
    "QueueAdapter",
    "MockQueueAdapter",
    "DeadLetterSink",
    "InMemoryDeadLetterSink",
    "route_for",
    "context_of",
    "package",
    "summary_of",
    "metadata",
    "handoffs",
    "bootstrap",
]
