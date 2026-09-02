"""A6 Frequency Cap & Precedence Engine (POA/05 / M05).

    from services.event_pipeline.frequency import FrequencyPrecedenceEngine, EngagementLedger

Called by A5 (M04) with the in-session matches; returns an EngagementDecision
whose reservation_id M08 confirms/rolls back.
"""
from __future__ import annotations

from . import bootstrap
from .engine import FrequencyPrecedenceEngine
from .ledger import EngagementLedger
from .lock import NullLock, PerCustomerLock
from .precedence import choose_winner
from .tables import engagements, metadata

__all__ = [
    "FrequencyPrecedenceEngine",
    "EngagementLedger",
    "PerCustomerLock",
    "NullLock",
    "choose_winner",
    "metadata",
    "engagements",
    "bootstrap",
]
