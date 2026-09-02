"""M14 — Audit, Reporting & Analytics (POA/14).

    from services.analytics import AnalyticsService, OutcomeEvent, OutcomeKind

See `metrics.py` for why every rate names its denominator and why a zero
denominator returns None, and POA/14 §11 for the PII boundary between aggregates
and drill-down.
"""
from .events import (
    EVENT_FIELDS,
    SEGMENT_FIELDS,
    OutcomeEvent,
    OutcomeKind,
    Segment,
    as_row,
)
from .metrics import KIND_TO_COUNT, Counts, Metrics, rate
from .service import AnalyticsService, AnalyticsStore

__all__ = [
    "AnalyticsService",
    "AnalyticsStore",
    "Counts",
    "EVENT_FIELDS",
    "KIND_TO_COUNT",
    "Metrics",
    "OutcomeEvent",
    "OutcomeKind",
    "SEGMENT_FIELDS",
    "Segment",
    "as_row",
    "rate",
]
