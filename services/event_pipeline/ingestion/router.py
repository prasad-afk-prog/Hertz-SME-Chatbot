"""FastAPI ingestion router (POA/02 §3.1) — the single write-door.

Endpoints: POST /v1/events, POST /v1/events:batch. Auth + the ingestion service
are read from ``app.state`` (wired in build_app). Outcome -> HTTP status:
accepted/duplicate -> 202, identity_conflict -> 409, rate_limited -> 429,
store outage -> 503, malformed body -> 422 (FastAPI validation).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError

from generator.models import Event

from .auth import Principal
from .schemas import BatchAck, BatchIn, IngestAck
from .service import IngestionService, IngestOutcome

router = APIRouter(prefix="/v1", tags=["ingestion"])

_OUTCOME_STATUS = {
    IngestOutcome.identity_conflict: status.HTTP_409_CONFLICT,
    IngestOutcome.rate_limited: status.HTTP_429_TOO_MANY_REQUESTS,
}


def _service(request: Request) -> IngestionService:
    svc = getattr(request.app.state, "ingestion_service", None)
    if svc is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "event store not configured")
    return svc


def _principal(request: Request) -> Principal:
    return request.app.state.authenticator.authenticate(request)


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
def ingest_event(
    event: Event,
    svc: IngestionService = Depends(_service),
    principal: Principal = Depends(_principal),
) -> IngestAck:
    try:
        outcome = svc.ingest(event, principal)
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "event store unavailable") from exc
    if outcome in _OUTCOME_STATUS:
        raise HTTPException(_OUTCOME_STATUS[outcome], outcome.value)
    return IngestAck(event_id=event.event_id, status=outcome.value)


@router.post("/events:batch", status_code=status.HTTP_202_ACCEPTED)
def ingest_batch(
    body: BatchIn,
    svc: IngestionService = Depends(_service),
    principal: Principal = Depends(_principal),
) -> BatchAck:
    try:
        results = svc.ingest_batch(body.events, principal)
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "event store unavailable") from exc
    accepted = sum(1 for r in results if r.status == "accepted")
    duplicates = sum(1 for r in results if r.status == "duplicate")
    failed = len(results) - accepted - duplicates
    return BatchAck(accepted=accepted, duplicates=duplicates, failed=failed, results=results)
