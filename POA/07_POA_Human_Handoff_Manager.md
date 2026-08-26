# POA — Human Handoff Manager

**Module ID:** M07 | **Flow stage:** 2 | **Flow nodes:** HM, Z3 | **Status:** Draft
**Depends on:** M04 (routes handoff events), M13 (routing rules) | **Feeds:** support/agent queue, M14

---

## 1. Purpose & scope

Handle escalation from bot to human as a **first-class, admin-configured** step (plan change: not
ad-hoc inside the conversation flow). Receives handoff events (raised by M12, routed via M04),
applies admin-configured routing rules (skill, team, language, priority), and dispatches the
conversation — **with full context attached** — into the existing support/agent queue (Z3).
Logs the handoff for reporting (M14).

**In scope:** routing-rule evaluation, context packaging, dispatch to the agent queue, handoff
logging, SLA/priority tagging.
**Out of scope:** deciding *to* hand off (M12 raises the event), the agent tooling itself.

## 2. Functional requirements
- Consume handoff events routed by M04.
- Apply **routing rules** (from M13): map context (skill needed, region/language, customer type,
  priority) → destination queue/team/agent skill group.
- **Package full context:** conversation transcript, triggering signal, customer profile/booking
  history, verified-claim results, and why the bot escalated.
- Dispatch into the existing support/agent queue (adapter to Zendesk/Salesforce/in-house — TBD).
- Log every handoff (Z3 → M14 handoff-rate metric) and emit audit trail.
- Handle **no available agent / after-hours** gracefully (fallback queue, callback, templated msg).

## 3. Technical design

### 3.1 Routing rules (config, M13)
```jsonc
{
  "rule_id": "region_lang_priority_v1",
  "match": {"language": "de", "customer_type": "corporate"},
  "route": {"queue": "de-corporate", "skill": "billing", "priority": "high"},
  "sla": {"first_response": "PT5M"},
  "fallback_queue": "general-de"
}
```
- Ordered rules, first match wins; explicit default/fallback route.

### 3.2 Context package
- Assembled from the conversation store (M08/M12), M03 signal, profile/booking history, and M10
  verification outcomes. Delivered as a structured payload + human-readable summary the agent sees
  immediately.

### 3.3 Queue adapter
- Pluggable adapter interface (`enqueue(handoff_payload) -> ticket_ref`) with a concrete
  implementation for the chosen support platform. Retry + dead-letter on adapter failure.

### 3.4 Lifecycle
- Track handoff status (`raised → routed → accepted → resolved`) for reporting; correlate the
  ticket_ref back to the conversation for closed-loop attribution (M14).

## 4. Technology & dependencies
- Support-platform SDK/API (TBD), M13 routing config, M03/M08 for context, M14 logging.

## 5. Task breakdown
1. Handoff event contract + intake from M04.
2. Routing-rule model + evaluator (share DSL with M04/M05 where possible).
3. Context packager (transcript + profile + signal + verification).
4. Queue adapter interface + concrete impl for chosen platform.
5. After-hours / no-agent fallback handling.
6. Handoff lifecycle tracking + M14 logging + audit.

## 6. Acceptance criteria
- A handoff event is routed to the correct queue/team per configured rules, with full context
  attached, and appears in the agent's tool ready to pick up.
- Every handoff is logged (feeds handoff-rate) and audited.
- No-agent/after-hours cases follow the configured fallback, never drop silently.
- Routing rules are changeable via admin console without redeploy.

## 7. Testing strategy
- Unit: routing-rule evaluation incl. fallback/default.
- Integration: handoff event → routed ticket in a sandbox of the support platform.
- Resilience: adapter failure → retry/dead-letter; verify no lost handoffs.

## 8. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Support platform unknown/late | adapter abstraction, start with a stub/queue table |
| Context too large/PII-heavy for tickets | summarise + link, apply PII policy (M15) |
| Misrouting frustrates customers | deterministic rules + observable in M14, tunable in M13 |
| Handoff lost on adapter outage | retry + dead-letter + alert |

## 9. Effort & sequencing
Phase 3, after M12 raises handoff events. ~2–3 weeks (plus platform-integration unknowns).

## 10. Open questions
1. Which support/agent platform is the target (Zendesk / Salesforce / in-house)?
2. What agent-queue routing dimensions exist today (skills, teams, languages)?
3. After-hours policy: callback, fallback queue, or templated "we'll get back to you"?
