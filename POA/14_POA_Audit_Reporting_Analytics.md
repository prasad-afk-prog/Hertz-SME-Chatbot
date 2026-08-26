# POA — Audit, Reporting & Analytics

**Module ID:** M14 | **Flow stage:** 5 | **Flow nodes:** AL–AO | **Status:** Draft
**Depends on:** M03, M13, outcome events from M05/M07/M12 | **Feeds:** Admin dashboard

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
