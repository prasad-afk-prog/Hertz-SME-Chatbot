# POA — Audit, Reporting & Analytics

**Module ID:** M14 | **Flow stage:** 5 | **Flow nodes:** AL–AO
**Status:** **Implementation landed 2026-09-02** (Shagun, Track B) — see §11. Dashboard deferred.
**Depends on:** M03, M13, outcome events from M05/M07/M12 | **Feeds:** Admin dashboard

---

## 11. Implementation status (2026-09-02)

Code: `services/analytics/` — `events.py`, `metrics.py`, `service.py`.
Tests: `tests/test_analytics_service.py` (35 tests; 472 in the suite, all green).

| # | Task | Status |
|---|------|--------|
| 1 | Outcome-event contracts | ✅ `OutcomeEvent` + adapters for M09/M10/M11/M12/M13 — **nothing upstream had to change** |
| 2 | Analytics event store + immutable ingestion | ✅ frozen events, reads return tuples |
| 3 | Metric rollups | ✅ recomputed from raw, never incremented — see below |
| 4 | Attribution join | ✅ uses M12's rules; pending is its own bucket |
| 5 | Reporting API (aggregates, drill-down, filters, export) | ◐ query layer + CSV done; **HTTP surface deferred with M13's** |
| 6 | Dashboard | ✗ **frontend stack; §10.3 unanswered** |
| 7 | Config-audit views (from M13) | ✅ read-only view + CSV export |

### Every rate names its denominator

This is where a reporting module gets silently wrong, and the four in §3.2 do **not** share one:

| Rate | Numerator | Denominator | Source |
|------|-----------|-------------|--------|
| conversion | converted | **fired** | A6 approvals |
| engagement | responded | **delivered** | M11 receipts |
| handoff | handed_off | **conversations** (responded + no_engagement) | M12 outcomes |
| suppression | suppressed | **matched** (fired + suppressed) | A6 |

Conversion is deliberately over *fired*, not *delivered*: an engagement that was approved and then
failed to deliver is a conversion we **lost**, and moving it into the denominator would flatter the
number.

**A zero denominator returns `None`, never `0.0`.** "0% conversion" and "no data" are different
facts, and a dashboard that renders them identically lies to whoever is deciding from it.

### Aggregates are recomputed, never incremented

`metrics()` recounts from the raw events on every call. A cached counter that drifts from its
source is the classic reporting bug and it drifts *silently*. §6 requires numbers to reconcile with
raw outcome events, and one code path makes that true by construction — the test asserts it as a
property over every event kind, not a spot-check. If this ever needs to be fast, the fix is a
materialised view over the same events (§3.1), not a hand-maintained counter.

### Pending is a real bucket

M12 leaves a resolved-but-not-yet-booked conversation's terminal state as `None` (POA/12 §11).
In M14 that is neither a conversion nor a non-conversion — it is **pending**. Bucketing it as
not-converted would under-report conversion for every conversation still inside its attribution
window; dropping it would stop the funnel adding up. So it is counted, and
`converted + pending + not_converted == conversations` is asserted. `pending` is clamped at zero,
because a conversion can land in a later window than its resolution.

The funnel is also asserted never to widen — a funnel that grows at a later stage is a counting
bug, not an insight.

### The PII boundary, stated precisely

§8's fourth risk is PII in reports. An `OutcomeEvent` carries only identifiers, enums and counts,
and a test asserts its fields are disjoint from everything `pii.PII_FIELDS` marks — so **aggregates
and exports are PII-free by construction**, the same trick M08 uses for its context bundle. A
second test walks the whole S4 fixture corpus against a CSV export.

That does **not** make drill-down PII-free. §3.3's drill-down reaches conversations, and
`HandoffEvent.transcript` is free customer text. M14 only ever ingests a handoff's *reason*, never
its transcript — but following a `conversation_id` from a drill-down into the conversation store
reaches customer text, and that path needs **M15's access control, which does not exist yet**.
Aggregates are safe; drill-down is not, and this is the boundary.

**Open questions — current state:**

- **§10.1 (launch KPIs, attribution rules)** — the rates above are implemented with documented
  denominators, so product is changing a definition rather than commissioning code.
- **§10.2 (Postgres aggregates vs. warehouse)** — moot for now: recount-from-raw is correct at any
  size that fits memory, and the fix at scale is a materialised view over the same events.
- **§10.3 (extend the M13 admin app or a separate BI surface)** — unanswered, and it decides task 6.
- **§10.4 (reporting roles, analytics retention)** — unanswered; ties to the drill-down boundary
  above and to M15.

**Not yet wired, and not inventable alone:** suppression (Z1) and deferred-expiry (Z2) events
exist upstream in A6 and A7, but nothing currently *emits* them to M14 — `OutcomeKind.suppressed`
and `deferred_expired` are defined and counted, with no producer. That is task 1's remaining half
and a cross-track contract; added to POA/18 §5b.

---

## 1. Purpose & scope

Close the loop: turn every outcome into visibility and control. Attribute conversions to the
triggering intervention (AL), log handoffs and feed the handoff-rate (AM), track engagement /
no-engagement rates per trigger (AN), and roll it all up into the **Reporting API / Admin
dashboard** (AO). Also surface the config-change audit trail (from M13/AQ) for compliance.

**In scope:** outcome event collection, metric computation (conversion, handoff, engagement,
suppression), reporting API, admin dashboard, audit-trail views, exports.
**Out of scope:** producing the outcomes (upstream modules emit them).

## 2. Functional requirements
- Ingest outcome/analytics events from across the pipeline:
  - **Conversion attributed (AL):** from M12 (booking after intervention) → per-trigger conversion.
  - **Handoff logged (AM):** from M07 → handoff-rate, routing breakdown, resolution.
  - **Engagement rate (AN):** from M12/M11 (responded vs. no-response, per trigger).
  - **Suppressions (Z1):** from M05 → suppressed volume + reasons.
  - **Deferred expiries (Z2):** from M06.
  - **Fallback usage / verification corrections:** from M09/M10 (quality signals).
- Compute metrics per trigger, segment (customer type/region/language), and time window.
- **Reporting API** + **admin dashboard** (AO) with drill-down.
- **Audit trail views** of config changes (from M13) for compliance/traceability.
- Exports (CSV) and, optionally, scheduled reports.

## 3. Technical design

### 3.1 Data flow
- Modules emit structured **outcome events** to a dedicated stream/table (`analytics_events`) — or
  reuse M03's stream with an analytics consumer group. Keep raw outcome events immutable.
- A rollup process (Celery periodic, or SQL views / materialised views) computes metric aggregates.
- For scale/BI later, optionally sink to a warehouse (open question).

### 3.2 Metric model (examples)
```
conversion_rate(trigger, window)   = converted / fired
engagement_rate(trigger, window)   = responded / delivered
handoff_rate(trigger, window)      = handoffs / conversations
suppression_rate(trigger, window)  = suppressed / matched
fallback_rate, verification_correction_rate  (quality)
```
- Attribution join: conversation → trigger → outcome, within attribution window (rules from M12).

### 3.3 Reporting API + dashboard
- FastAPI read API (aggregates + drill-down + filters by trigger/segment/time).
- Dashboard: funnel (signal → engaged → resolved → booked), per-trigger performance, handoff view,
  quality panel (fallback/verification), and the config audit log. (UI can share the M13 admin app.)

### 3.4 Audit surface
- Read views over `config_audit` (M13) and engagement/handoff logs; immutable, filterable, exportable.

## 4. Technology & dependencies
- Postgres (aggregates / materialised views) or warehouse, FastAPI reporting API, dashboard SPA
  (shared with M13), Celery (rollups). Consumes outcome events from all stages.

## 5. Task breakdown
1. Define outcome-event contracts emitted by M05/M06/M07/M09/M10/M11/M12.
2. Analytics event store + immutable ingestion.
3. Metric rollups (materialised views / Celery jobs).
4. Attribution join logic (with M12 rules).
5. Reporting API (aggregates, drill-down, filters, export).
6. Dashboard (funnel, per-trigger, handoff, quality, audit).
7. Config-audit views (from M13).

## 6. Acceptance criteria
- Conversions are attributed to the correct trigger and visible per trigger/segment/time.
- Engagement, handoff, suppression rates are computed and match source events.
- Admins can drill from an aggregate to underlying conversations/events.
- The config-change audit trail is viewable and exportable for compliance.
- Numbers reconcile with raw outcome events (spot-check parity).

## 7. Testing strategy
- Unit: metric formulas, attribution join.
- Data-integrity: aggregate parity vs. raw events.
- Integration: emit synthetic outcomes end-to-end → dashboard reflects them.

## 8. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Attribution disputes | explicit, documented attribution window/rules; drill-down transparency |
| Metric/raw divergence | immutable events + reconciliation checks |
| Dashboard slow on big data | pre-aggregated materialised views / warehouse |
| PII in reports | aggregate-first, access-controlled drill-down (M15) |

## 9. Effort & sequencing
Phase 4 (needs outcomes from earlier phases), but define the outcome-event contracts in Phase 1 so
modules emit them from the start. ~3–4 weeks.

## 10. Open questions
1. Required KPIs/definitions for launch (conversion attribution rules especially)?
2. Postgres aggregates vs. dedicated warehouse/BI tool?
3. Dashboard: extend the M13 admin app or a separate BI surface?
4. Reporting access roles and data-retention for analytics?
