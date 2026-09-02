"""A7 Pending-Engagement Queue & Deferred Scheduler (POA/06 / M06).

    from services.event_pipeline.pending import PendingQueue, PendingScheduler

PendingQueue is A5's DeferredSink; PendingScheduler.on_login re-evaluates eligible
entries through A6 and fires the winner; the Celery Beat sweep expires the rest.
"""
from __future__ import annotations

from . import bootstrap
from .queue import PendingEntry, PendingQueue
from .scheduler import PendingScheduler
from .tables import metadata, pending_engagements
from .tasks import beat_schedule, register_pending_tasks

__all__ = [
    "PendingQueue",
    "PendingScheduler",
    "PendingEntry",
    "beat_schedule",
    "register_pending_tasks",
    "metadata",
    "pending_engagements",
    "bootstrap",
]
