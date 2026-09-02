# POA — Event Ingestion API (FastAPI)

**Module ID:** M02 | **Flow stage:** 2 | **Flow nodes:** K | **Status:** Draft
**Depends on:** M03 (Event Store) | **Consumes from:** M01 | **Feeds:** M03 → M04

---

## 1. Purpose & scope

A **FastAPI** service that receives, validates, normalises and persists every behavioural event
from the portal/widget, then hands it to the Event Store for durable storage and near-real-time
fan-out. This is the single write-door into the backend pipeline.

**In scope:** HTTP API, auth, validation, normalisation, idempotency, rate limiting, write to
Event Store, backpressure.
**Out of scope:** trigger logic (M04), storage internals (M03).

## 2. Functional requirements
- Accept single and **batched** events (M01 batches).
- **Validate** against the canonical event schema (Pydantic v2); reject malformed with clear errors.
- **Normalise**: canonical signal types, UTC timestamps, enum coercion, PII field allow-listing.
- **Idempotent**: duplicate `event_id` is accepted-but-deduped (safe client retries).
- **Authenticate** the portal as a trusted source (service auth) and bind the event to the
  authenticated customer identity (do not trust `customer_id` from the body blindly — cross-check).
- **Low latency** ack (< 100ms p95) — persist + enqueue, return; heavy work is downstream.
- **Backpressure / rate limiting** per source and per customer.

## 3. Technical design

### 3.1 Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v1/events` | ingest one event |
| POST | `/v1/events:batch` | ingest a batch |
| GET | `/healthz`, `/readyz` | liveness/readiness |
| GET | `/metrics` | Prometheus metrics |

### 3.2 Canonical event model (owns the contract shared with M01)
Pydantic models mirror the M01 contract; the API is the authority on the accepted schema and its
versioning (`sdk_version` / `schema_version` negotiation, additive-only changes preferred).

### 3.3 Processing pipeline (per event)
```
auth → schema validate → identity bind/verify → normalise → PII allow-list
     → idempotency check (event_id) → write to Event Store (M03)
     → publish to Redis Stream → 202 Accepted
```
- Write path is **write-through**: persist to Postgres (source of truth) and publish to the Redis
  Stream so M04 can consume with low latency. Use an **outbox** pattern (or M03-provided transactional
  publish) so a stored event is never lost from the stream.

### 3.4 Errors
- 400 schema invalid, 401/403 auth, 409 conflicting identity, 422 semantic, 429 rate-limited,
  503 when Event Store is unavailable (client buffers & retries — see M01).

## 4. Technology & dependencies
- FastAPI + Uvicorn/Gunicorn, Pydantic v2, `asyncpg`/SQLAlchemy async, `redis-py` (async).
- Auth: mTLS or signed service token from the portal; JWT/session validation for identity binding.
- Depends on M03 for the write+publish primitive.

## 5. Task breakdown
1. Scaffold FastAPI service (config, logging, health, metrics) — reuse M15 skeleton.
2. Implement Pydantic contract + schema-version handling.
3. Auth + identity-binding middleware.
4. Idempotency (dedupe on `event_id`, e.g. unique constraint / Redis SET NX).
5. Normalisation + PII allow-list.
6. Write-through to M03 with outbox/transactional publish.
7. Rate limiting + backpressure.
8. Batch endpoint with partial-success semantics.

## 6. Acceptance criteria
- Valid events return 202 and appear in the Event Store and on the Redis Stream exactly once.
- Duplicate `event_id` does not create duplicate stored events.
- Malformed/unauthorised requests are rejected with correct status + machine-readable error.
- p95 ack latency < 100ms under expected load; graceful 503 + retry when M03 is down.

## 7. Testing strategy
- Unit: validation, normalisation, idempotency, identity binding.
- Contract tests shared with M01.
- Integration: end-to-end write to a test Postgres + Redis, assert exactly-once on stream.
- Load test to validate latency and rate-limit behaviour; chaos test M03 outage.

## 8. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Lost events between Postgres write and stream publish | transactional outbox pattern |
| Spoofed customer_id | server-side identity binding, don't trust body |
| Traffic spikes | rate limit + horizontal scale + Redis buffering |
| Schema drift with M01 | versioned, additive-only contract + contract tests in CI |

## 9. Effort & sequencing
Phase 1, immediately after/with M03. ~2 weeks.

## 10. Open questions
1. Portal→API auth mechanism (mTLS vs signed token)?
2. Expected event volume (events/sec peak) for sizing?
3. Is identity available as a verifiable token, or only as a body field?

## 11. Build notes / deviations (A4 — 2026-09-01)

Delivered in `services/event_pipeline/ingestion/`, on the A1 template, writing
through the A2 Event Store. All 8 §5 tasks implemented; the client-gated pieces
sit behind seams so the open questions don't block code.

- **Endpoints** (`router.py`): `POST /v1/events`, `POST /v1/events:batch`, plus the
  template's health/metrics. Outcome→status: accepted/duplicate → **202**,
  identity_conflict → **409**, rate_limited → **429**, store outage → **503**,
  malformed → **422**.
- **Validation + PII allow-list**: the single-event body is the canonical
  `generator.models.Event` (design principle P3), so FastAPI validates it and
  `extra='forbid'` on the event/context **is** the field allow-list (POA/15 §4) —
  an unknown/PII field is a 422 (tested). Deeper redaction is Track B's S4
  (`generator/pii.py`) and integrates when merged.
- **Auth (§10.1 open)**: behind a `SourceAuthenticator` protocol —
  `ApiKeyAuthenticator` (shared-secret, constant-time) today; mTLS or a signed/JWT
  token slot in behind the same seam, and a JWT variant would set
  `Principal.customer_id` so **identity binding** (§3.3) becomes a real cross-check.
  Open (`AllowAllAuthenticator`) only when no key is set, and logged loudly.
- **Idempotency**: delegated to the store (dedupe on `event_id`); a retry is a
  202 `duplicate`, never a second row (tested). Redis SET-NX fast-path noted as a
  future optimisation.
- **Write-through**: uses A2's transactional outbox (persist + enqueue in one txn);
  the relay fans out to the stream. End-to-end API→store→outbox→relay→stream is
  tested.
- **Rate limiting (§5.7)**: `RateLimiter` seam with an in-memory fixed-window
  default (per source+customer); prod uses a Redis-backed counter (§8).
- **Batch (§5.8)**: `:batch` validates each item independently for **partial
  success** — valid items are written even if others are malformed (tested).

**Deferred:** the real auth mechanism (gated on §10.1), distributed Redis rate
limiting, and p95-latency / load verification (needs a live Postgres+Redis
environment; docker-compose is provided). Async SQLAlchemy is a future option —
kept sync for consistency with the rest of the services layer.

**Acceptance (§6):** valid→202 + stored + on the stream exactly once; duplicate
deduped; malformed/unauthorised rejected with the right status. Latency-target
verification awaits the live environment.
