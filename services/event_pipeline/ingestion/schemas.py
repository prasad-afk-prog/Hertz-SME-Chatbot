"""Request/response models for the ingestion API (POA/02 §3.1).

Single ingest takes the canonical ``generator.models.Event`` as the body (FastAPI
validates it → 422 on malformed; ``extra='forbid'`` on the event/context IS the
PII field allow-list, POA/15 §4). The batch endpoint takes raw items so each is
validated independently for partial success.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class IngestAck(BaseModel):
    event_id: str
    status: str                       # accepted | duplicate


class BatchItemResult(BaseModel):
    index: int
    event_id: str | None = None
    # accepted | duplicate | invalid | identity_conflict | rate_limited
    status: str
    detail: str | None = None


class BatchAck(BaseModel):
    accepted: int
    duplicates: int
    failed: int
    results: list[BatchItemResult]


class BatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[dict] = Field(default_factory=list)   # raw; validated per-item in the service
