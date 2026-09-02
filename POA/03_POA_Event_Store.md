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

## 11. Build notes / deviations (A2 — 2026-09-01)

Delivered in `services/event_pipeline/store/` on the shared platform template.

**Done (§5 tasks 1–5):**
- **Schema** (`tables.py`) — `events` (append-only, PK `event_id` for idempotency) +
  `event_outbox`, with the §3.1 indexes. JSON columns are real **JSONB on Postgres**
  and plain JSON on SQLite (`JSON().with_variant(JSONB, "postgresql")`), and the
  outbox PK is `BigInteger().with_variant(Integer, "sqlite")` — bigserial in prod,
  autoincrement rowid in tests.
- **Transactional write+publish** (`store.py::write_event`) — inserts `events` +
  `event_outbox` in one transaction; idempotent (existence check + `IntegrityError`
  backstop → returns False on a duplicate `event_id`, never double-enqueues).
- **Outbox relay** (`relay.py`) — publish FIRST, then mark; per-row marking so a
  mid-batch failure never re-publishes delivered rows. At-least-once; consumers
  dedupe on `event_id`. **Property-tested**: no event lost under a flaky publisher
  (§7), and idempotent writes (§7).
- **Redis Streams** (`publisher.py`) — `RedisStreamPublisher` (XADD to `events:in`,
  approximate `maxlen` trim) + `ensure_group("trigger-eval")` for M04/A5. The relay
  depends only on a `StreamPublisher` protocol, so it's tested against an in-memory
  publisher with no Redis.
- **Read models** (§3.4) — `recent_events`, `session_events`, `last_event_at`, and
  `has_repeated_search` (signal I). Wired a `postgres` readiness check into the
  event-pipeline app (`app.state.event_store` is what A4 will write through).

**Deferred (honest scope):**
- §5.6 retention / monthly-partition-pruning job — gated on open question 1; the
  schema comments mark the partition key.
- §5.7 signal **J** (`last_booking_at` / dormancy) — that timestamp lives in the
  booking/customer store, not the event log, so it's a cross-store query, not A2.
- Live Redis consumer-group integration test (needs a running Redis — docker-compose
  is provided; unit tests cover the publish contract with a fake).
- **Async** SQLAlchemy + **Alembic** migrations: kept **sync** (consistent with the
  rest of the services layer; avoids pytest-asyncio in shared deps) and used
  `bootstrap.create_all` for local/dev + tests. Prod migrations remain Alembic's job.

**Acceptance (§6):** exactly-once handoff + idempotency are verified under induced
failures. Latency-target and retention/consumer-resume criteria await a live
Postgres/Redis integration environment.
