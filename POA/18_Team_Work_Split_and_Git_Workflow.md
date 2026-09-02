# Team Work-Split & Git Workflow — Prasad & Shagun

**Purpose:** Two people building the same POA-driven system in parallel, pushing
and merging every day. This file exists so neither person guesses what the
other is doing, no module gets built twice, and merges at day-end are boring
(no file-level collisions).

**Status:** v1.1 — 2026-09-01. **Track ownership is CONFIRMED** — Prasad takes
Track A, Shagun takes Track B (agreed 2026-09-01). Two items in §8 remain open:
the git workflow (§6) and the service-code repo layout.

---

## 1. Why split by *track*, not by *day* or by *file*

The module table in `00_Master_POA_Index.md` already has a dependency graph.
Splitting a shared backlog randomly day-to-day causes exactly the duplicate
work / wasted tokens problem we're trying to avoid. Instead we cut the graph
**once**, into two chains that:

- rarely depend on each other's *unfinished* work (each person keeps moving
  even if the other is mid-module), and
- live in **separate top-level directories**, so two people editing at the
  same time almost never touch the same file → merges are additive, not
  conflicting.

## 2. Sprint 0 — finish the test-dataset backlog first (before any service code)

`POA/16 §16` was resolved on 2026-08-31 and left a **prioritised backlog** that
is the genuine next work. The generator/mocks are shared foundation for *both*
tracks, so this gets cleared first, split by file to stay collision-free:

| # | Item (POA/16 §16) | Touches | Owner |
|---|---|---|---|
| S1 | Expand taxonomy & stations — 12 classes, city/suburban, one-way | `generator/world.py`, `catalogues.py`, `config.py` | Prasad |
| S2 | Fee completeness — late-return, no-show, fuel + dispute scenarios | `generator/customers.py`, `durations.py`, `scenarios.py` | Prasad |
| S3 | Conversation-intent scenarios + scripted trees (17 intents) — **DONE 2026-09-01** | `generator/intents.py` (new), `models.py`, `pipeline.py`, `cli.py` | Shagun |
| S4 | PII redaction fixtures — obvious + embedded | new fixture module + `tests/` | Shagun |
| S5 | Load/SLA config knobs — eps, concurrency, mock timeouts | `generator/config.py`, `volume.py` | Prasad |
| S6 | Future-client-compat layer — repository interface + `field_map.yaml` + lenient DTO | new `generator/repository.py` | Shagun |

S1/S2/S5 and S3/S4/S6 are chosen so the two people don't edit the same file.

**Three exceptions, stated plainly rather than discovered in a conflict:**

- **`generator/models.py`** — both halves need it. S1's 12 vehicle classes and
  city/suburban stations touch `VehicleCategory`/`LocationType`; S2's
  late-return/no-show/fuel charges need fee fields; S3 already added the
  `Intent`/`Conversation*` block. Rule in §4: **announce, don't just push**.
  Mitigation that has worked so far: append one *bounded, contiguous* block at
  the end of the relevant section rather than editing throughout — git merges
  those cleanly even when both people go in the same day.
- **`generator/pipeline.py`** — both halves add writers for their new output.
  Same rule: keep the diff to contiguous appended lines.
- **`tests/conftest.py`** — keep new fixtures in your own test module unless a
  fixture is genuinely shared.

Service-code tracks (§3) start once Sprint 0 is merged, or earlier for whoever
finishes their half first.

## 3. The two tracks

### Track A — Event & Trigger Pipeline + Platform (owner: **Prasad** — confirmed)

Rationale: Prasad built the foundational layer (`generator/`, `mocks/`,
`tests/`, contract models, POA 16/17) — this track is the direct continuation:
turning the event/trigger side into real services.

| Order | Module | POA file | Depends on |
|---|---|---|---|
| A1 | Platform skeleton (repo layout, CI, secrets, logging/tracing baseline) | `15_POA_...Observability.md` | — |
| A2 | Event Store (Postgres + Redis Streams) | `03_POA_Event_Store.md` | A1 |
| A3 | Customer Journey & Behavioural Event Capture (SDK/contract) | `01_POA_...Event_Capture.md` | A4 contract |
| A4 | Event Ingestion API (FastAPI) | `02_POA_Event_Ingestion_API.md` | A2 |
| A5 | Trigger Evaluation Engine | `04_POA_Trigger_Evaluation_Engine.md` | A2, A6 |
| A6 | Frequency Cap & Precedence Engine | `05_POA_Frequency_Cap_Precedence.md` | A2 |
| A7 | Pending-Engagement Queue & Deferred Scheduler (Celery) | `06_POA_...Scheduler.md` | A2, A5 |
| A8 | Human Handoff Manager | `07_POA_Human_Handoff_Manager.md` | A5 |

Code lives under: `services/event_pipeline/` (+ `services/platform/` for A1).

### Track B — Conversation, Config & Reporting (owner: **Shagun** — confirmed)

| Order | Module | POA file | Depends on |
|---|---|---|---|
| B1 | Admin Console & Trigger Configuration (persistence + CRUD) | `13_POA_Admin_Console_Trigger_Config.md` | A2 |
| B2 | Conversation Orchestrator | `08_POA_Conversation_Orchestrator.md` | A2, B3, B4, B5 |
| B3 | LLM Integration & Fallback Service | `09_POA_LLM_Integration_Fallback.md` | B2 |
| B4 | Claim Verification Service | `10_POA_Claim_Verification_Service.md` | booking-API mock (built) |
| B5 | Chatbot UI Integration (HS-103) | `11_POA_...HS103.md` | HS-103 mock (built) |
| B6 | Customer Response & Multi-turn Conversation Manager | `12_POA_...Conversation_Manager.md` | B2, A5, A8 |
| B7 | Audit, Reporting & Analytics | `14_POA_Audit_Reporting_Analytics.md` | A2, B1 |

Code lives under: `services/conversation/`.

**Two things worth knowing when reading this table:**

1. **B1 does *not* block Track A** (corrected 2026-09-01 — an earlier draft of
   this file said it did). The M13 config **contract already exists**:
   `TriggerConfig`, `RoutingRule`, `FrequencyCap` and `Deferred` are in
   `generator/models.py`, with working defaults in `generator/fixtures.py`.
   Prasad can build A5/A6/A8 against those today. What B1 still owes is
   **persistence + admin CRUD**, not the schema.
   One real caveat: `RoutingRule.match`, `.route` and `.sla` are bare `dict`
   with no inner types. B1 will likely tighten them into real models, so A8
   should keep its handoff code thin around those three fields to avoid a
   rework.
2. **The master index nominally puts all of Track B in Phase 2**, downstream of
   Track A's Phase 1. Track B does **not** wait for that: build B2–B6 against
   the existing mocks and generated fixtures (`mocks/booking_api.py`,
   `mocks/llm_provider.py`, `mocks/hs103.py`, `mocks/support_queue.py`,
   `test_data/`) and swap to the real services as Track A ships them. That is
   what keeps both people unblocked all the way through.

## 4. Shared files — hands off unless announced

- **`generator/models.py`** — the contract models, and the single file both
  tracks genuinely share. Either person may need a field. **Rule: propose the
  change in the day-end message, don't just push it.** A silent model change
  breaks the other track's in-progress code. Adding a field is usually safe;
  renaming or removing one is not.
- **`generator/`, `mocks/`, `tests/`, `test_data/`** — outside your Sprint-0
  items (§2), consume these as fixtures. Extend only when a module needs a
  fixture that doesn't exist, and say so at day-end.
- **`POA/*.md`** — annotate your own modules' sections freely. Don't edit the
  other track's POA file without flagging it.
- **`README.md`, `pyproject.toml`, `requirements*.txt`** — touch only when
  actually adding a dependency or directory; keep the diff small and call it
  out, so the other person isn't surprised by a conflict here.

## 5. Cross-track contract points (the only real coupling)

Until the real module exists, build against the mocks/fixtures above so
neither person blocks on the other:

1. **Event schema** (A2/A4 → everything): `generator/models.py`. Track B codes
   against it as-is until the real Event Store ships.
2. **Trigger/config schema** (B1 → A5, A6, A8): Track A codes against
   `generator/fixtures.py` defaults until B1 ships. When B1 ships it must not
   silently rename fields Track A already relies on.
3. **"Fire" decision → conversation start** (A5/A6 → B2): define as a small
   Pydantic message/event, not a direct function call, so the services stay
   decoupled (matches the architecture's queue-based design).
4. **Handoff event** (A8 → B6): same — a message contract, not an in-process
   call.

Whoever needs a contract change adds it to `generator/models.py`, runs
`pytest`, and says so explicitly at day-end. Don't assume the other person
spots it in the diff.

## 6. Daily git workflow

**Branches:** each person works on their own long-lived branch, never on `main`.

- Prasad: `track-a/event-pipeline`
- Shagun: `track-b/conversation`

**During the day:** commit locally as often as you like. Small, scoped commits
— one module or one clear step each.

**Use `merge`, not `rebase`, on these branches.** They are long-lived and
already pushed; rebasing published commits forces a force-push, which is
forbidden below. Rebase is only safe for commits that never left your machine.

**End of day, in this order:**

```bash
# 1. Bring main into your branch FIRST, so conflicts get resolved locally
#    rather than as a surprise on GitHub.
git checkout track-a/event-pipeline      # or track-b/conversation
git fetch origin
git merge origin/main                    # merge, not rebase — see above
# resolve anything (should be rare — see the directory split in §3)

# 2. Push
git push origin track-a/event-pipeline

# 3. Open/update the PR into main. Self-review the diff for accidental
#    cross-track file touches, then merge.
#    (needs the GitHub CLI installed + `gh auth login` once)
gh pr create --base main --head track-a/event-pipeline --fill   # first time
gh pr merge --merge                                              # or via the UI

# 4. Whoever merges SECOND then re-syncs, so both branches start tomorrow
#    from the same base:
git checkout main && git pull
git checkout track-a/event-pipeline && git merge main && git push
```

**A 2-line async check-in before merging** (chat, not a meeting):
- which module(s) you finished/advanced today,
- any contract point (§5) you touched, or need from the other track.

The branch + directory split prevents *file* collisions. This check-in
prevents *logical* collisions — both quietly building the same module, or one
building on an assumption the other just invalidated.

**Never:**
- force-push, to any branch,
- rebase a branch that has already been pushed,
- start a module from the other track "just to help" without a heads-up —
  update §7 instead, so it's visible.

## 7. Status tracker

**Conflict rule (important):** this table is the one file both people would
otherwise edit daily. Each person edits **only their own rows** — Prasad the
A-rows, Shagun the B-rows, and each their own Sprint-0 rows. Never reformat or
rewrite the whole table, and **append** to a Notes cell rather than rewriting
it. Kept this way, git merges the two halves cleanly.

| Item | Owner | Status | Notes |
|---|---|---|---|
| S1 Taxonomy & stations | Prasad | in progress | 2026-09-01 implemented+tested locally: 12-class taxonomy (additive world pass-3, golden prices byte-stable), city/suburban + US/USD stations, one-way helpers + config knob |
| S2 Fee completeness | Prasad | in progress | 2026-09-01 implemented+tested locally: one-way + late-return/no-show/fuel fees & disputes, FeeLine/FeeDispute models, 5 dispute fixtures |
| S5 Load/SLA knobs | Prasad | in progress | 2026-09-01 implemented+tested locally: LoadProfile (eps/concurrency/SLA/mock-timeout) on GenConfig, exposed via volume + config/load_profile.yaml |
| A1 Platform skeleton | Prasad | in progress | 2026-09-01 done+tested: services/platform/ template (create_app: logging, correlation-id, Prometheus /metrics, OTel seam, health/readyz, errors, lazy pg/redis/celery factories) + booting services/event_pipeline/ + docker-compose + Dockerfile + CI + .env.example; repo layout ratified in POA/15 §12; 8 tests |
| A2 Event Store | Prasad | in progress | 2026-09-01 core done + downstream-ready: Postgres schema + idempotent transactional write+outbox, at-least-once Redis-stream relay (property-tested), read models (recent/session/repeated-search), postgres readiness. Runs on Postgres/prod + SQLite/tests. Deferred: retention/partition job, signal-J booking backfill, live-Redis integration (POA/03 §11) |
| A3 Event Capture SDK | Prasad | not started | |
| A4 Ingestion API | Prasad | in progress | 2026-09-01 core done + tested: POST /v1/events + /v1/events:batch (partial-success) writing through A2's outbox; Event-schema validation = PII allow-list (extra=forbid); auth + identity-binding + rate-limit behind seams (API key today, mTLS/JWT later); idempotent via store dedupe; 202/409/429/503/422 mapping; e2e API→store→relay→stream tested. Deferred: real auth mechanism (§10.1), Redis rate limiter, load verification (POA/02 §11) |
| A5 Trigger Evaluation | Prasad | in progress | 2026-09-01 core done + tested: sandboxed field/op/value rule DSL (no eval), node-N routing (in-session→A6 reserve→FireMessage/M08, deferred→queue, drop), idempotency guard, contracts as sinks not calls (§5). FireMessage added to generator/models.py (carries A6 reservation_id). e2e store→relay→parse→evaluate→fire tested. Deferred: I/J derivation workers (need A7), rule hot-reload, handoff branch (§10.3), consumer partitioning (POA/04 §11) |
| A6 Frequency/Precedence | Prasad | in progress | 2026-09-01 core done + tested: engagement ledger + sliding-window caps (delegates reference.would_fire), per-customer global cap + cooldown, deterministic precedence (weight→specificity→recency→id), atomic reserve under a per-customer lock (concurrency invariant tested), reserve→confirm/rollback so a failed send doesn't burn the cap, suppression reasons for M14. CROSS-TRACK: A6→M08 contract (MatchCandidate/EngagementDecision/SuppressionReason) added to generator/models.py — reconcile with Shagun's local §5 handshake. Deferred: Redis counters, reservation TTL sweep, M13 cap config (POA/05 §11) |
| A7 Pending Queue/Scheduler | Prasad | in progress | 2026-09-02 core done + tested: pending_engagements queue (PendingQueue IS A5's DeferredSink), time-travelled wait/eligibility/expiry, Celery Beat expiry sweep + reconcile, login re-eval hook (on_login → A6 arbitrate → FireMessage, precedence among pending, cap-suppressed stays pending), concurrency guard (5 logins → raise-once). e2e A5→A7→A6 tested. Deferred: in-session merge at login, SKIP-LOCKED claim, Beat integration (POA/06 §11) |
| A8 Human Handoff | Prasad | not started | keep thin around RoutingRule dicts |
| S3 Intent scenarios / scripted trees | Shagun | DONE | generator/intents.py, 17 intents, 23 tests |
| S4 PII redaction fixtures | Shagun | not started | |
| S6 Client-compat repository layer | Shagun | not started | |
| B1 Admin config model + CRUD | Shagun | not started | persistence + CRUD; contract already in models.py |
| B2 Conversation Orchestrator | Shagun | not started | |
| B3 LLM Integration/Fallback | Shagun | not started | |
| B4 Claim Verification | Shagun | not started | |
| B5 Chatbot UI (HS-103) + admin UI | Shagun | not started | |
| B6 Customer Response Manager | Shagun | not started | |
| B7 Audit/Reporting | Shagun | not started | |

Status values: `not started` → `in progress` → `PR open` → `merged`.

If this table proves annoying to keep merged in practice, move it to GitHub
Issues or a Project board — outside git, so it has no merge semantics at all.

## 8. Open questions

1. ~~**Confirm track ownership** (§3).~~ **RESOLVED 2026-09-01** — Prasad takes
   Track A (event/trigger pipeline + platform, POA 01–07 & 15), Shagun takes
   Track B (conversation, config, reporting, POA 08–14).
2. ~~**Repo layout for service code.**~~ **RESOLVED 2026-09-01** — ratified the
   `services/` monorepo this document assumed (`services/platform/` shared
   template, `services/event_pipeline/` Track A, `services/conversation/` Track
   B), recorded in `POA/15 §12`. Delivered by A1.
3. **Branch protection on `main`** — required PR review, no direct pushes.
   Worth enabling once both branches exist, so a bad merge can't land without
   the other person seeing the diff.
