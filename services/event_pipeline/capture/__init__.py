"""A3 Customer Journey & Behavioural Event Capture (POA/01 / M01).

    from services.event_pipeline.capture import CaptureClient, HttpTransport

The reference/server-side capture SDK: builds schema-valid Events for the eight
signals, correlates sessions, gates on consent, buffers, and flushes batches to
A4's Ingestion API without loss under a transient outage.
"""
from __future__ import annotations

from .buffer import EventBuffer
from .client import CaptureClient, FlushResult
from .session import Session, new_session
from .transport import HttpTransport, InMemoryTransport, Transport, TransportError

__all__ = [
    "CaptureClient",
    "FlushResult",
    "Session",
    "new_session",
    "EventBuffer",
    "Transport",
    "TransportError",
    "HttpTransport",
    "InMemoryTransport",
]
