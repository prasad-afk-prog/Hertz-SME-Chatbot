# POA — Event Store (Postgres + Redis Streams)

**Module ID:** M03 | **Flow stage:** 2 | **Flow nodes:** L | **Status:** Draft
**Depends on:** — (foundational) | **Consumed by:** M02 (write), M04/M05/M06/M08/M14 (read)

---

## 1. Purpose & scope

Provide **durable storage** (PostgreSQL) and **near-real-time delivery** (Redis Streams) for the
event pipeline. Postgres is the source of truth; the Redis Stream is the low-latency conduit that
feeds the Trigger Evaluation Engine. This module also owns the shared read models other modules
rely on (recent-event lookups, customer signal history for deferred derivation).

**In scope:** schema, partitioning/retention, the transactional write+publish primitive, stream
consumer groups, read APIs/queries used by downstream modules.
**Out of scope:** what events *mean* (M04).

## 2. Functional requirements
- Durable, append-only storage of every ingested event.
- **Exactly-once handoff** to the stream (no stored event missing from the stream, no phantom).
- Fast reads for: "recent events for customer X in session Y", "has customer X searched
  route/dates before" (feeds deferred signals I/J), "last booking timestamp" (feeds J).
- **Retention & partitioning** aligned with data-retention policy (M15).
- Consumer-group semantics so M04 can scale horizontally and resume after restart.

## 3. Technical design

### 3.1 Postgres schema (core)
```sql
-- append-only event log
CREATE TABLE events (
  event_id      uuid PRIMARY KEY,             -- idempotency key from client
  customer_id   text NOT NULL,
  session_id    text NOT NULL,
  signal_type   text NOT NULL,
  occurred_at   timestamptz NOT NULL,
  received_at   timestamptz NOT NULL DEFAULT now(),
  source        text NOT NULL,
  context       jsonb NOT NULL,
  consent       jsonb,
  schema_version text
);
CREATE INDEX idx_events_customer_time ON events (customer_id, occurred_at DESC);
CREATE INDEX idx_events_session ON events (session_id);
CREATE INDEX idx_events_signal ON events (signal_type, occurred_at DESC);
-- monthly range partition on occurred_at for retention/pruning

-- transactional outbox (guarantees stream publish)
CREATE TABLE event_outbox (
  id           bigserial PRIMARY KEY,
  event_id     uuid NOT NULL REFERENCES events(event_id),
  published_at timestamptz,
  payload      jsonb NOT NULL
);
```

### 3.2 Write + publish primitive (used by M02)
- In one DB transaction: insert into `events` (ON CONFLICT DO NOTHING for idempotency) and insert
  into `event_outbox`.
- A relay (in-process or Celery/loop) reads unpublished outbox rows → `XADD` to Redis Stream
  `events:in` → marks `published_at`. Guarantees at-least-once to the stream; consumers dedupe on
  `event_id` (exactly-once effect).

### 3.3 Redis Streams
- Stream `events:in`; consumer group `trigger-eval` (M04). Fields: event_id, customer_id,
  signal_type, session_id, occurred_at, context.
- `XAUTOCLAIM`/pending-entries handling for consumer restarts; maxlen/trim for stream size.

### 3.4 Read models / helper queries
- `recent_events(customer_id, since)`, `session_events(session_id)`.
- `repeated_search(customer_id, route, dates)` → supports signal I.
- `last_booking_at(customer_id)` / `days_since_last_booking` → supports signal J.

## 4. Technology & dependencies
- PostgreSQL 15+ (range partitioning), Redis 7+ (Streams).
- Migration tool: Alembic. Access via async SQLAlchemy / asyncpg.

## 5. Task breakdown
1. Schema + migrations (events, outbox, partitions).
2. Transactional write+publish primitive exposed to M02.
3. Outbox relay worker.
4. Redis Stream + consumer-group setup + pending/claim handling.
5. Read-model query functions + indexes.
6. Retention/partition-pruning job (coordinate with M15).
7. Backfill/derivation queries for deferred signals I/J.

## 6. Acceptance criteria
- Every event M02 writes is both persisted and delivered to `events:in` exactly once (verified
  under induced failures between the two steps).
- Downstream read queries meet latency targets (recent-events p95 < 50ms).
- Retention job prunes/archives old partitions without impacting live writes.
- Consumer group resumes cleanly after M04 restart with no lost/duplicated processing.

## 7. Testing strategy
- Property/chaos test the outbox relay (kill between insert and publish → no loss).
- Idempotency test (same event_id twice → one row, one effective stream consume).
- Load test read models against realistic volumes.

## 8. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Dual-write inconsistency (DB vs stream) | transactional outbox + relay, never direct dual write |
| Stream unbounded growth | maxlen trimming + retention policy |
| Hot partition / index bloat | monthly partitions, targeted indexes, autovacuum tuning |
| PII in `context` jsonb | field allow-list at M02, encryption-at-rest, retention limits |

## 9. Effort & sequencing
Phase 0 foundation — build first; M02/M04 depend on it. ~2–3 weeks.

## 10. Open questions
1. Data-retention window for raw events (legal/Hertz policy)?
2. Redis: single vs cluster; is it shared infra or dedicated?
3. Do we need an analytical copy (warehouse) for M14, or query Postgres directly?
