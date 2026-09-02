# POA — Pending-Engagement Queue & Deferred Scheduler

**Module ID:** M06 | **Flow stage:** 2 | **Flow nodes:** P, R, S, Z2 | **Status:** Draft
**Depends on:** M03, M04 | **Feeds:** M05→M08 (on re-eval) / Z2 (expiry)

---

## 1. Purpose & scope

Handle **deferred** triggers: matches that shouldn't act immediately but the next time the customer
logs in (within a validity window). Stores them in the Pending-Engagement Queue (P), runs a
scheduled expiry sweep (R, via Celery Beat), and on the customer's next login re-checks validity (S)
— routing valid ones back through the frequency/precedence check (O/M05), and discarding expired
ones (Z2).

**In scope:** pending queue storage, Celery Beat expiry sweep, login-hook re-evaluation, expiry
discard + logging.
**Out of scope:** deciding *what* is deferred (M04), the cap check itself (M05).

## 2. Functional requirements
- Persist deferred engagements with `created_at`, `wait_period`, `expiry`, trigger + context.
- **Expiry sweep (R):** periodic Celery Beat job walks the queue, expiring entries past their
  `expiry` (→ Z2, discarded unraised, logged for M14).
- **Login re-evaluation (S):** on customer login, fetch valid pending entries and pass them into
  M05 (frequency/precedence) → if approved, fire via M08. This is node S "logs in again before
  expiry? → yes → O".
- **Wait period:** an entry may also have a minimum wait before it is eligible (not just an expiry).
- Idempotent: an entry is raised at most once; concurrent logins don't double-fire.

## 3. Technical design

### 3.1 Queue schema
```sql
CREATE TABLE pending_engagements (
  id           uuid PRIMARY KEY,
  customer_id  text NOT NULL,
  trigger_id   text NOT NULL,
  context      jsonb NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  eligible_at  timestamptz NOT NULL,      -- created_at + wait_period
  expires_at   timestamptz NOT NULL,      -- created_at + expiry
  status       text NOT NULL DEFAULT 'pending' -- pending|raised|expired
);
CREATE INDEX idx_pending_customer_status ON pending_engagements (customer_id, status);
CREATE INDEX idx_pending_expiry ON pending_engagements (expires_at) WHERE status='pending';
```

### 3.2 Expiry sweep (Celery Beat)
- Beat schedule (e.g. every 5 min) → task `expire_pending()`:
  `UPDATE ... SET status='expired' WHERE status='pending' AND expires_at < now()` (batched),
  emit Z2 analytics events for each. Uses `SKIP LOCKED` batching for scale.

### 3.3 Login re-evaluation hook
- Login events arrive as ordinary events (M01/M02) or via a dedicated login signal. On login:
  1. Fetch `pending` entries for the customer where `eligible_at <= now() < expires_at`.
  2. Atomically claim them (row lock / status→`raising`) to avoid double-fire.
  3. Feed to M05 arbitration alongside any in-session matches (precedence may prefer a fresher
     in-session signal). Approved → M08; mark `raised`. Not approved → keep or drop per policy.
- Consider firing on the *next meaningful signal* after login rather than the raw login, to keep
  context relevant — decide with product (open question).

## 4. Technology & dependencies
- Celery + Celery Beat, Redis broker, Postgres queue table, M05 arbitration, M08 fire.

## 5. Task breakdown
1. Pending-engagement schema + migration.
2. Enqueue API used by M04 (with wait/expiry from trigger config).
3. Celery Beat expiry sweep + Z2 logging.
4. Login re-evaluation hook + atomic claim.
5. Integration with M05/M08.
6. Reconciliation for stuck `raising` rows.

## 6. Acceptance criteria
- A deferred match is stored and, on the customer's next eligible login within window, is
  re-evaluated and (if approved) fired exactly once.
- Entries past expiry are swept to `expired`, never raised, and logged (Z2).
- Concurrent logins never double-raise the same entry.
- Wait period is honoured (not eligible before `eligible_at`).

## 7. Testing strategy
- Time-travel tests (freeze/advance clock) for eligibility and expiry.
- Concurrency test on the login claim.
- Integration: enqueue → login → fire path.

## 8. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Double-firing on rapid logins | atomic status claim + unique raise guard |
| Sweep lag leaves stale entries | short Beat interval + expiry check also at login |
| Queue growth for dormant customers | expiry + retention pruning |
| Deferred message feels out-of-context at login | fire on next signal, not raw login (config) |

## 9. Effort & sequencing
Phase 3. ~2–3 weeks (after M04/M05/M08 exist).

## 10. Open questions
1. Fire deferred on raw login, or on the next in-session signal after login?
2. Default wait/expiry per deferred trigger?
3. Do multiple pending entries for one customer compete via precedence, or FIFO?

## 11. Build notes / deviations (A7 — 2026-09-02)

Delivered in `services/event_pipeline/pending/`, on the A1 template, reusing A6
(arbitration) + the `FireMessage` contract + A5's `DeferredItem`.

- **Queue** (`tables.py`, `queue.py`): `pending_engagements` with
  `eligible_at = created + wait_period`, `expires_at = created + expiry`, status
  pending→raising→raised / expired. `PendingQueue.enqueue` **is** A5's DeferredSink
  (§10.3 answered by wiring, not duplication). Event context for re-eval is
  fetched from A2 by `event_id` rather than duplicated into the queue.
- **Time-travel**: every read takes an explicit `now`, so wait/eligibility/expiry
  are tested by advancing a passed clock (§7).
- **Expiry sweep (R)** `expire_due(now)` marks overdue **pending** rows expired and
  returns their ids for Z2 logging — never touches raised/raising (tested).
- **Login re-evaluation (S)** `PendingScheduler.on_login(customer, now)`: under a
  per-customer lock, fetch eligible entries → arbitrate through **A6** (multiple
  pending entries **compete on precedence** — §10.3 answered: precedence, not
  FIFO) → fire the winner via the FireSink, mark `raised`; losers stay pending for
  a later login; cap-suppressed entries stay pending. Concurrency test: 5 parallel
  logins raise **at most once** (§6).
- **Reconcile** `reconcile_stuck(now, older_than)` releases rows stuck in `raising`
  (crash between claim and raise) back to pending (§5.6).
- **Celery** (`tasks.py`): `beat_schedule()` + `register_pending_tasks(celery_app,
  queue)` wire the sweep/reconcile to Beat. No top-level celery import (the worker
  passes its app), so the module imports in the API/test process; task bodies are
  thin wrappers over the directly-tested queue methods.

**Deviations / deferred:** §10.1 — fires on **login** (not "next in-session
signal"); the in-session merge at login is a documented extension (A5 would pass
its in-session candidates in). Atomic claim uses the per-customer lock (prod:
`SKIP LOCKED`). Beat wiring is integration-run, not unit.

**Acceptance (§6):** deferred match stored and raised once on next eligible login
✓, expiry sweep discards+logs and never raises ✓, concurrent logins never
double-raise ✓, wait period honoured ✓.
