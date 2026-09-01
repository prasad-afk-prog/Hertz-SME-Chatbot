# POA — Frequency Cap & Precedence Engine

**Module ID:** M05 | **Flow stage:** 2 | **Flow nodes:** O, Z1 | **Status:** Draft
**Depends on:** M03, M13 | **Called by:** M04 | **Feeds:** M08 (approved) / Z1 (suppressed)

---

## 1. Purpose & scope

Given one or more in-session trigger matches for a customer, decide **whether to engage at all**
(frequency cap: not re-engaged too recently) and **which single trigger wins** if several match at
once (precedence). Approve → fire (M08); reject → "no engagement raised" (Z1), logged for analytics.

**In scope:** frequency-cap accounting, precedence arbitration, atomic "reserve engagement slot".
**Out of scope:** matching rules (M04), sending the message (M08).

## 2. Functional requirements
- **Frequency cap:** per-trigger and/or global caps (e.g. max 1 engagement per trigger per 7 days,
  max N per customer per day). Config-driven (M13).
- **Precedence:** deterministic winner among simultaneous matches (by `precedence` weight, then a
  tiebreak — e.g. most specific / most recent signal).
- **Atomicity:** the check-and-reserve must be race-free across concurrent events for the same
  customer (no double engagement squeaking past the cap).
- **Suppression accounting:** every Z1 suppression is logged with reason (which cap, which
  precedence loss) for M14 reporting.
- **Cooldowns:** respect a global "quiet period" after any engagement if configured.

## 3. Technical design

### 3.1 Cap accounting
- Redis counters keyed by `{customer_id}:{trigger_id}` and `{customer_id}:global` with TTL windows;
  authoritative ledger of engagements also in Postgres (for audit + window recompute).
- Sliding vs fixed window: default **sliding** window using sorted-sets (ZADD timestamps, ZCOUNT in
  range) for accuracy; fixed-window counters as a cheaper option (config).

### 3.2 Atomic reserve
```
reserve_engagement(customer, candidates[]):
  acquire per-customer lock (Redis SETNX / Redlock or DB advisory lock)
  filter candidates by frequency cap (check counters/ledger)
  if none remain -> return SUPPRESSED(reason=cap)
  winner = max(precedence, tiebreak) over remaining
  record tentative engagement (counter incr + ledger row, status=reserved)
  release lock
  return APPROVED(winner)
```
- The ledger row is finalised when M08 actually delivers (or rolled back if delivery fails), so a
  failed send doesn't burn the customer's cap. Coordinate the confirm/rollback contract with M08.

### 3.3 Precedence rules
- Primary: `precedence` weight (higher wins). Tiebreak order (config): specificity of match →
  most recent signal → stable trigger id. Fully deterministic.

## 4. Technology & dependencies
- Redis (counters, sorted sets, locks), Postgres (engagement ledger), M13 (cap/precedence config).

## 5. Task breakdown
1. Engagement ledger schema + migration.
2. Redis window counters (sliding sorted-set implementation).
3. Atomic reserve with per-customer lock.
4. Precedence arbitration + deterministic tiebreak.
5. Reserve→confirm/rollback contract with M08.
6. Suppression reason logging → M14.
7. Config wiring for caps/cooldowns (M13).

## 6. Acceptance criteria
- Under concurrent matching events for one customer, at most the configured number of engagements
  fire (verified by stress test).
- The highest-precedence trigger wins deterministically; ties broken per config.
- A delivery failure in M08 releases the reserved slot (cap not consumed).
- Every suppression is logged with a machine-readable reason.

## 7. Testing strategy
- Unit: window math (sliding), precedence/tiebreak truth tables.
- Concurrency: N parallel reserves → invariant "≤ cap" holds.
- Integration: reserve→confirm and reserve→rollback with M08 stub.

## 8. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Lock contention hotspots | short-held per-customer locks, fast Redis ops |
| Counter/ledger divergence | ledger is source of truth; counters rebuildable from it |
| Reserved-but-never-confirmed leaks | TTL on reservations + reconciliation sweep |
| Cap feels too aggressive/lax | admin-tunable via M13, observable via M14 |

## 9. Effort & sequencing
Phase 1, with M04. ~2 weeks.

## 10. Open questions
1. Default caps and global daily maximum per customer?
2. Sliding vs fixed window as the default?
3. Is there a global cross-trigger cooldown after any engagement?

## 11. Build notes / deviations (A6 — 2026-09-01)

Delivered in `services/event_pipeline/frequency/` on the A1 template, over A2's
storage pattern.

- **Engagement ledger** (`tables.py`, `ledger.py`) — the Postgres source of truth
  (`engagements`: reservation_id, customer, trigger, reserved_at, status). Only
  `reserved`/`confirmed` rows count; `rolled_back` is ignored. Portable schema
  (bigserial/rowid via with_variant) → Postgres in prod, SQLite in tests.
- **Cap accounting** delegates to `reference.would_fire` — the executable
  sliding-window spec the invariant suite already asserts — so the service and
  the spec can't diverge. Sliding window is the default (§10.2); per-trigger cap
  from `TriggerConfig.frequency_cap`, optional per-customer `global_cap`, optional
  ISO-8601 `cooldown`.
- **Atomic reserve** (`engine.py`) under a per-customer lock (`lock.py`):
  cooldown → global cap → per-trigger filter → precedence winner → reserve a
  ledger row. Lock is a seam — in-process `threading` default, Redis/advisory in
  prod. A concurrency test (10 threads, 1 customer, cap 1) asserts exactly one
  engagement fires (§6).
- **Precedence** (`precedence.py`) — deterministic: precedence weight → match
  specificity (condition count) → most-recent signal → lowest trigger id.
  Order-independent (tested).
- **reserve → confirm/rollback** (POA/05 §3.2): `EngagementDecision.reservation_id`
  is the handle; `confirm()` finalises, `rollback()` frees the slot so a failed
  M08 delivery doesn't burn the cap (tested). **Cross-track**: this handshake is
  shared with M08 — the contract models are in `generator/models.py`
  (`MatchCandidate`, `EngagementDecision`, `SuppressionReason`); reconcile names
  with Shagun's local POA/18 §5 edit when it lands.
- **Suppression accounting** (§2): every non-approval carries a machine-readable
  `suppression_reason`, and `losers` maps each dropped trigger → reason for M14.

**Deferred:** Redis counters (the ledger is authoritative; counters are a
rebuildable cache), the reserved-but-never-confirmed TTL reconciliation sweep,
and admin/M13 wiring of default caps/cooldowns (values are engine params now).
No HTTP surface — A6 is a library invoked by A5.

**Acceptance (§6):** ≤ cap under concurrency ✓, deterministic precedence ✓,
rollback frees the slot ✓, every suppression logged with a reason ✓.
