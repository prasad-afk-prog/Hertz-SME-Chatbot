# POA — Admin Console & Trigger Configuration

**Module ID:** M13 | **Flow stage:** 5 | **Flow nodes:** AP, AQ
**Status:** **Service layer landed 2026-09-02** (Shagun, Track B) — see §11. API/UI/RBAC deferred.
**Depends on:** M03 (config storage) | **Feeds:** M04/M05/M06/M07 (config), M14 (audit)

---

## 11. Implementation status (2026-09-02)

Code: `services/admin/config/` — `models.py`, `validation.py`, `service.py`.
Tests: `tests/test_admin_config_service.py` (38 tests; 320 in the suite, all green).

§9 asks for "data model + basic CRUD early so M04/M05 have config". That is what landed: the
service layer, versioned and audited, with the HTTP surface and UI deferred.

| # | Task | Status |
|---|------|--------|
| 1 | Config data model + migrations | ◐ model + in-memory store done; **Postgres migrations need Prasad's A1** |
| 2 | Admin CRUD API + validation + versioning/rollback | ◐ all the logic done; **the FastAPI surface is deferred** — see below |
| 3 | Config-change audit writer (AQ) | ✅ append-only, actor + before/after, immutable by test |
| 4 | Hot-reload pub/sub + consumer contract | ◐ publisher + version stamps done; **the consumer contract is unagreed** (POA/18 §5) |
| 5 | RBAC + admin auth | ✗ **needs M15's auth model** (Prasad's A1) and §10.3's role matrix |
| 6 | Admin UI | ✗ **§10.1 unanswered** — standalone SPA or embedded in the HFB portal admin? |
| 7 | Dry-run / preview | ✗ **needs the shared rule-DSL validator** — see below |

### The rule-DSL seam — a hole with a shape, deliberately

`TriggerMatch.conditions` is `list[dict]` with no schema. §8's mitigation for "DSL divergence from
M04" is a **single shared validator library**, and M04 is Prasad's and does not exist yet.

Writing a second validator here would manufacture exactly the divergence §8 warns about: the
runtime would accept rules the console rejects, or worse, the reverse. So everything *around* the
DSL is validated — schema, enums, template refs, precedence, caps, durations, deferred-field
coherence — and `conditions` goes through a named `DSLValidator` protocol whose default,
`NullDSLValidator`, validates nothing **and says so**: publishing a rule with conditions produces
an advisory issue telling the admin it went through unchecked. A visible hole is more useful to
whoever fills it than a guess or a silence.

Task 7 (dry-run "what would match") falls out as deferred for the same reason — a preview built on
a matcher M04 does not share would mislead rather than help.

### Decisions worth carrying forward

- **Rollback creates a version; it never deletes one.** Restoring v3 produces v5 whose definition
  equals v3's, with `restored_from` recorded. Overwriting would punch a hole in the audit exactly
  where someone reverted a bad config — the moment the trail matters most.
- **The audit is immutable in practice, not just in intent.** Entries are frozen dataclasses and
  reads return a tuple, so history cannot be edited through a returned value. §7 asks for this test
  by name.
- **Instant disable bypasses validation.** §6's emergency brake has to work on a live misbehaving
  config even when that config could no longer be published.
- **Precedence must be distinct across *enabled* triggers.** `fixtures.py` gives every signal its
  own value (200 down to 50) because M04/M05 pick a winner when two triggers match. A disabled
  trigger does not squat on a value.
- **Template refs are checked against M09's real `FallbackCatalogue`**, not a string pattern, so
  renaming a template fails here.
- **Publishing emits a version stamp, not a payload** — `(entity, entity_id, version, enabled)`.
  A consumer detects staleness and re-reads from a source it already trusts; a payload would bake
  in a wire format invented on Prasad's behalf, plus a stale-payload race.
- **`generator/fixtures.py` is read, never written.** It is the dataset's seed config and Prasad's
  file under POA/18 §2; M13 seeds from it and owns its own store. A test pins that.
- **The shipped fixtures pass their own validator.** A validator its seed data fails is one nobody
  trusts.

### Why the HTTP surface is deferred

§3.2 specifies FastAPI. That is a new dependency in `requirements.txt` — a shared file under
POA/18 §4 — and the `services/` layout is still unconfirmed after six modules. The service layer is
dependency-free and transport-agnostic, so adding routes later is additive. Not worth spending
Prasad's coordination budget on before the layout question is answered.

**Open questions — current state:**

- **§10.1 (SPA or embedded admin UI)** — unanswered, and it decides the whole of task 6.
- **§10.2 (maker/checker approval workflow)** — unanswered, and it is a real fork in the data
  model, not a UI detail: an approval step means a version can exist in a *pending* state, which
  the current model has no room for. Worth answering before task 2's API is built rather than after.
- **§10.3 (admin roles / RBAC matrix)** — unanswered; blocks task 5 alongside M15.

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
