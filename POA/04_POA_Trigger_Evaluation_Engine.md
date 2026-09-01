# POA — Trigger Evaluation Engine

**Module ID:** M04 | **Flow stage:** 2 | **Flow nodes:** M, N | **Status:** Draft
**Depends on:** M03 (events), M05 (freq/precedence), M13 (config) | **Feeds:** M05/M06/M07/M08

---

## 1. Purpose & scope

The brain of the pipeline. It consumes events from the Event Store stream, evaluates them (and
scheduled deferred checks, and handoff events) against **admin-configured trigger rules**, and
decides the **event type**: in-session, deferred, or handoff. It routes accordingly.

**In scope:** stream consumption, rule matching, event-type decision (node N), routing to
downstream stages, deferred-signal derivation (I/J), consuming handoff events fed back from M12.
**Out of scope:** the frequency/precedence *arbitration* itself (M05) and the deferred *scheduling*
(M06) — this engine calls into them.

## 2. Functional requirements
- Consume `events:in` (consumer group) with at-least-once + idempotent processing.
- Match each event against active trigger definitions (from M13, hot-reloaded/cached).
- **Node N decision:**
  - **In-session** match → call M05 (frequency cap & precedence). If approved → fire (M08).
  - **Deferred** match (signals I/J, or any rule marked deferred) → write to Pending-Engagement
    Queue (M06).
  - **Handoff event** (fed back from M12 via M04) → route to Human Handoff Manager (M07).
- Derive deferred signals I (repeated cross-session search) and J (dormant) using M03 read models
  + scheduled scans.
- Support **multiple concurrent matches** (hand to M05 for precedence).
- Config-driven: no code change to add/modify a trigger.

## 3. Technical design

### 3.1 Rule model (defined/owned with M13)
```jsonc
{
  "trigger_id": "abandon_payment_v1",
  "enabled": true,
  "match": {                         // predicate over event + customer context
    "signal_type": "booking_abandoned",
    "conditions": [{"field": "context.step", "op": "in", "value": ["payment","review"]}]
  },
  "type": "in_session",              // in_session | deferred
  "deferred": {"wait_period": "PT0S", "expiry": "P3D"},
  "frequency_cap": {"per": "P7D", "max": 1},
  "precedence": 100,                 // higher wins (M05)
  "personalisation_hints": {...},    // passed to M08
  "message_template_ref": "tmpl_abandon_payment"
}
```

### 3.2 Evaluation flow
```
stream event ─► load active rules (cached) ─► evaluate predicates
   ├─ 0 matches ─► drop (log for analytics)
   ├─ in_session match(es) ─► M05 arbitrate ─► approved? ─► M08 fire / else Z1 suppressed
   ├─ deferred match ─► M06 enqueue (with wait/expiry)
   └─ handoff event ─► M07 route
```
- Rules cached in-process with TTL + pub/sub invalidation on M13 changes (config feedback loop).
- Predicate evaluation via a small, sandboxed rule DSL (no arbitrary code) — field/op/value.
- **Deferred derivation workers** (Celery/M06-scheduled): periodically scan M03 read models to
  emit synthetic I/J match candidates into the same evaluation path.

### 3.3 Idempotency & ordering
- Dedup on `event_id` (already deduped at ingest, re-checked here).
- Per-customer processing should avoid races (e.g. hash-partition consumers by customer_id) so
  frequency/precedence checks see a consistent view.

## 4. Technology & dependencies
- Redis Streams consumer (async), Postgres (rules cache source via M13), Celery (deferred scans).
- Rule DSL: a vetted expression evaluator (e.g. json-logic style), not `eval`.

## 5. Task breakdown
1. Stream consumer w/ consumer group, idempotency, per-customer partitioning.
2. Rule loader + cache + invalidation (integrate M13).
3. Predicate/DSL evaluator + test suite.
4. Node-N router (in-session / deferred / handoff).
5. Integrate M05 (arbitration) and M06 (enqueue) and M07 (handoff).
6. Deferred derivation workers for signals I and J.
7. Suppression logging (Z1 path emits analytics event to M14).

## 6. Acceptance criteria
- A configured in-session trigger, on a matching event, produces a fire decision (post-M05) or a
  logged suppression (Z1).
- A deferred trigger enqueues to M06 with correct wait/expiry.
- A handoff event routes to M07 with context.
- Toggling a trigger in the admin console takes effect without redeploy (config feedback loop).
- Signals I and J are correctly derived and evaluated.

## 7. Testing strategy
- Unit: DSL evaluator (truth tables), router decisions.
- Integration: stream → decision → downstream stub, per branch of node N.
- Concurrency: two matching events for same customer don't both fire past the cap.
- Config hot-reload test.

## 8. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Race between concurrent events bypassing cap | per-customer partitioning + M05 atomic check |
| Rule DSL becomes a code-exec hole | restricted, whitelisted operators only |
| Stale rule cache | pub/sub invalidation + short TTL |
| Deferred scans overload DB | batched, off-peak, indexed read models |

## 9. Effort & sequencing
Phase 1 core. ~3–4 weeks. Central dependency for M05/M06/M07/M08.

## 10. Open questions
1. Rule DSL: build minimal in-house vs adopt json-logic/CEL?
2. Frequency of deferred (I/J) scans and dormancy threshold source?
3. How are handoff events represented on their way back from M12 (stream vs direct call)?

## 11. Build notes / deviations (A5 — 2026-09-01)

Delivered in `services/event_pipeline/triggers/`, on the A1 template, wiring A2
(stream) → A5 (match/route) → A6 (arbitrate) → M08.

- **Rule DSL** (`dsl.py`): a minimal in-house, sandboxed field/op/value evaluator
  (§10.1 answered: in-house, not json-logic/CEL for now) — ops
  eq/ne/in/not_in/gt/gte/lt/lte/exists over dotted attribute paths, no `eval`
  (§8). `matching_rules` = signal match + all conditions. Truth-tabled.
- **Node-N routing** (`evaluator.py`): per event — idempotency guard → match →
  deferred matches to `DeferredSink` (M06/A7), in-session matches to A6.reserve;
  approved → `FireMessage` on `FireSink` (M08/B2), suppressed → `SuppressionSink`
  (Z1 → M14), no match → dropped.
- **Contracts as messages, not calls** (POA/18 §5): the fire decision is a
  `FireMessage` (in `generator/models.py`) carrying A6's `reservation_id`; every
  downstream is a sink protocol with an in-memory default (Redis/queue in prod).
- **Delegation**: cap/precedence is A6's — A5 just hands it `MatchCandidate`s —
  and matching is the DSL, so A5 owns no arbitration logic of its own.
- **Idempotency** (§3.3): an `IdempotencyGuard` seam (in-memory default) dedupes
  re-delivered stream events so an at-least-once redelivery can't double-fire.
- **Stream** (`consumer.py`): `RedisTriggerConsumer` (XREADGROUP `events:in` /
  group `trigger-eval` → evaluate → XACK); `parse_stream_fields` reconstructs the
  Event from the relay's fields (round-trip tested). An end-to-end test drives
  store → relay → parse → evaluate → fire with no Redis.

**Deferred:** deferred-signal (I/J) derivation *workers* (§5.6 — needs A7/Celery
scheduling; the deferred *routing* path is done), rule-cache hot-reload/pub-sub
(§5.2 — RuleSource seam is in place), the handoff branch (§10.3 open; M07/M12
absent), and per-customer consumer partitioning (§3.3 — the A6 lock already keeps
the cap safe). Consumer loop is integration-tested (docker), not unit.

**Acceptance (§6):** in-session match → fire-or-suppression ✓, deferred match →
enqueue ✓, no double-fire past the cap under repeat/redelivery ✓. Config
hot-reload + I/J derivation await A7 + M13.
