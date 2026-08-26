# POA — Admin Console & Trigger Configuration

**Module ID:** M13 | **Flow stage:** 5 | **Flow nodes:** AP, AQ | **Status:** Draft
**Depends on:** M03 (config storage) | **Feeds:** M04/M05/M06/M07 (config), M14 (audit)

---

## 1. Purpose & scope

Give admins full control **without code changes**: create/enable/disable/tune triggers, set wait
periods and frequency caps, and configure handoff routing rules (AP). Every configuration change is
recorded in an audit log (AQ). This is the "config-not-code feedback loop" that feeds the Trigger
Evaluation Engine and the other config-driven modules.

**In scope:** config data model + versioning, admin CRUD API + UI, validation, safe rollout
(enable/disable, staging), config-change audit, hot-reload notification to consumers.
**Out of scope:** the runtime that consumes config (M04/M05/M06/M07), reporting (M14).

## 2. Functional requirements
- CRUD for **triggers** (match rules, type, wait/expiry, frequency cap, precedence, personalisation
  hints, message/fallback template refs).
- CRUD for **handoff routing rules** (M07) and global settings (caps, cooldowns, dormancy period).
- **Enable/disable** and **precedence** management with validation (no conflicting/ambiguous rules,
  DSL validity, referenced templates exist).
- **Versioning:** every change creates a new version; support view/diff and rollback.
- **Audit (AQ):** who changed what, when, before/after — immutable, feeds M14/compliance.
- **Hot reload:** publish change notifications so M04/M05 etc. refresh caches without redeploy.
- **RBAC:** only authorised admins; sensitive actions gated.

## 3. Technical design

### 3.1 Config data model
```sql
CREATE TABLE trigger_configs (
  trigger_id text, version int, definition jsonb,
  enabled boolean, updated_by text, updated_at timestamptz,
  PRIMARY KEY (trigger_id, version)
);
CREATE TABLE config_current (   -- fast active-config view
  trigger_id text PRIMARY KEY, version int, definition jsonb, enabled boolean
);
CREATE TABLE routing_rules (rule_id text, version int, definition jsonb, ...);
CREATE TABLE global_settings (key text PRIMARY KEY, value jsonb, version int, ...);
CREATE TABLE config_audit (
  id bigserial PRIMARY KEY, entity text, entity_id text,
  action text, before jsonb, after jsonb, actor text, at timestamptz
);
```

### 3.2 Admin API + UI
- REST API (FastAPI) for config CRUD, validate, publish, rollback, list versions/audit.
- Admin UI (SPA) — trigger list with enable/disable toggles, rule editor with live validation, cap
  & wait-period controls, routing-rule editor, audit/version history + diff. (UI stack TBD; can be
  a React admin app or embedded in the existing portal admin.)

### 3.3 Validation
- Schema-validate the rule DSL (shared validator with M04), check referenced templates exist,
  detect precedence/cap conflicts, dry-run against sample events (preview which events would match).

### 3.4 Hot-reload propagation
- On publish: update `config_current`, write audit row, emit a pub/sub "config changed" event;
  consumers (M04/M05/M06/M07) invalidate caches. Version stamps let consumers detect staleness.

## 4. Technology & dependencies
- FastAPI admin API, Postgres (config + audit), Redis pub/sub (invalidation), admin SPA, auth/RBAC
  (M15). Shared DSL validator with M04.

## 5. Task breakdown
1. Config data model + migrations (triggers, routing, globals, audit).
2. Admin CRUD API + validation + versioning/rollback.
3. Config-change audit writer (AQ).
4. Hot-reload pub/sub + consumer contract.
5. RBAC + admin auth.
6. Admin UI (trigger management, rule editor, caps/waits, routing, audit/diff).
7. Dry-run/preview ("what would match").

## 6. Acceptance criteria
- An admin can create/enable/disable/tune a trigger and change caps/wait periods; changes take
  effect at runtime without a deploy.
- Every change is captured in an immutable audit log with actor + before/after.
- Invalid configs (bad DSL, missing template, conflicts) are rejected with clear errors.
- Versions can be viewed, diffed and rolled back.
- Only authorised admins can make changes (RBAC enforced).

## 7. Testing strategy
- Unit: validation, versioning/rollback, audit writing.
- Integration: publish → consumer hot-reload takes effect (with M04).
- Security: RBAC enforcement; audit immutability.
- UX: admin flows for the common tasks.

## 8. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Bad config breaks live engagements | validation + dry-run preview + instant disable + rollback |
| Config/runtime drift | version stamps + pub/sub invalidation + staleness detection |
| Unauthorised/erroneous changes | RBAC + immutable audit + approval workflow (optional) |
| DSL divergence from M04 | single shared validator library |

## 9. Effort & sequencing
Phase 0/1 foundation (data model + basic CRUD early so M04/M05 have config; UI can follow).
~3–4 weeks incl. UI.

## 10. Open questions
1. Admin UI: standalone SPA or embed in existing HFB portal admin?
2. Is a change-approval workflow (maker/checker) required for compliance?
3. Who are the admin roles/permissions (RBAC matrix)?
