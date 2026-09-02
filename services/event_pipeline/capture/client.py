"""CaptureClient (A3 / M01) — the reference/server-side capture SDK (POA/01).

Builds schema-valid ``generator.models.Event`` objects for the eight signals,
correlates them into a session, gates on consent, buffers them, and flushes
batches to A4's Ingestion API — retrying without loss on a transient outage. The
production client is a JS SDK on the portal (§4.1 hybrid); this Python client is
the server-side emission path, the contract reference, and the QA harness (§6.7).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from generator.models import (
    BookingStep,
    Consent,
    Event,
    EventContext,
    SignalType,
    Source,
)

from .buffer import EventBuffer
from .session import Session, new_session
from .transport import Transport, TransportError


@dataclass
class FlushResult:
    sent: int
    ok: bool
    ack: dict | None = None


class CaptureClient:
    def __init__(
        self,
        transport: Transport,
        customer_id: str,
        *,
        consent: Consent | None = None,
        session: Session | None = None,
        source: Source = Source.portal,
        max_buffer: int = 1000,
        schema_version: str = "1.0.0",
    ) -> None:
        self.transport = transport
        self.customer_id = customer_id
        self.consent = consent or Consent()
        self.session = session or new_session(customer_id)
        self.source = source
        self.schema_version = schema_version
        self.buffer = EventBuffer(max_buffer)

    def start_session(self, session_id: str | None = None) -> Session:
        """Begin a new session (a fresh login); subsequent captures use its id."""
        self.session = new_session(self.customer_id, session_id)
        return self.session

    def capture(
        self, signal_type: SignalType, *, occurred_at: datetime | None = None, **context
    ) -> bool:
        """Build + buffer one event. Returns False (dropped) when analytics
        consent is off — behavioural tracking is consent-gated (POA/01 §3, §8)."""
        if not self.consent.analytics:
            return False
        event = Event(
            event_id=uuid.uuid4().hex,                 # client-generated, for idempotency
            customer_id=self.customer_id,
            session_id=self.session.session_id,
            signal_type=signal_type,
            occurred_at=occurred_at or datetime.now(UTC),
            source=self.source,
            context=EventContext(**context),
            consent=self.consent,
            schema_version=self.schema_version,
        )
        self.buffer.add(event)
        return True

    # --- in-session signal detectors (C–H) — correct payload per §7 -------- #
    def search_no_convert(self, *, pickup, dropoff, pickup_at, return_at,
                          vehicle_class=None, occurred_at=None) -> bool:
        return self.capture(
            SignalType.search_no_convert, occurred_at=occurred_at,
            pickup=pickup, dropoff=dropoff, pickup_at=pickup_at,
            return_at=return_at, vehicle_class=vehicle_class,
        )

    def rate_view_no_progress(self, *, pickup, dropoff, pickup_at, return_at,
                              vehicle_class=None, occurred_at=None) -> bool:
        return self.capture(
            SignalType.rate_view_no_progress, occurred_at=occurred_at,
            pickup=pickup, dropoff=dropoff, pickup_at=pickup_at,
            return_at=return_at, vehicle_class=vehicle_class,
        )

    def booking_abandoned(self, *, step: BookingStep, occurred_at=None, **search) -> bool:
        return self.capture(
            SignalType.booking_abandoned, occurred_at=occurred_at, step=step, **search
        )

    def error_hit(self, *, error_code: str, occurred_at=None, **search) -> bool:
        return self.capture(
            SignalType.error_hit, occurred_at=occurred_at, error_code=error_code, **search
        )

    def extended_dwell(self, *, dwell_ms: int, occurred_at=None, **search) -> bool:
        return self.capture(
            SignalType.extended_dwell, occurred_at=occurred_at, dwell_ms=dwell_ms, **search
        )

    def session_ended_no_booking(self, *, occurred_at=None) -> bool:
        return self.capture(SignalType.session_ended_no_booking, occurred_at=occurred_at)

    # --- delivery --------------------------------------------------------- #
    def flush(self) -> FlushResult:
        """Send the buffered batch. On a transport failure the buffer is KEPT for
        the next flush, so no event is lost (POA/01 §7)."""
        events = self.buffer.snapshot()
        if not events:
            return FlushResult(sent=0, ok=True)
        payload = {"events": [e.model_dump(mode="json") for e in events]}
        try:
            ack = self.transport.send_batch(payload)
        except TransportError:
            return FlushResult(sent=0, ok=False)      # retried on the next flush
        self.buffer.clear()
        return FlushResult(sent=len(events), ok=True, ack=ack)
