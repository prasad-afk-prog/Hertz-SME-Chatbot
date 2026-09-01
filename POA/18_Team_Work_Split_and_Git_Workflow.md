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
| S3 | Conversation-intent scenarios + scripted trees (17 intents) | `generator/patterns.py` (new intent module), `mocks/llm_provider.py` | Shagun |
| S4 | PII redaction fixtures — obvious + embedded | new fixture module + `tests/` | Shagun |
| S5 | Load/SLA config knobs — eps, concurrency, mock timeouts | `generator/config.py`, `volume.py` | Prasad |
| S6 | Future-client-compat layer — repository interface + `field_map.yaml` + lenient DTO | new `generator/repository.py` | Shagun |

S1/S2/S5 and S3/S4/S6 are chosen so the two people don't edit the same file.
`generator/models.py` is the exception — see §4 for the rule.

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
| A5 | Trigger Evaluation Engine | `04_POA_Trigger_Evaluation_Engine.md` | A2, A6, **B1** |
| A6 | Frequency Cap & Precedence Engine | `05_POA_Frequency_Cap_Precedence.md` | A2, **B1** |
| A7 | Pending-Engagement Queue & Deferred Scheduler (Celery) | `06_POA_...Scheduler.md` | A2, A5 |
| A8 | Human Handoff Manager | `07_POA_Human_Handoff_Manager.md` | A5, **B1** |

Code lives under: `services/event_pipeline/` (+ `services/platform/` for A1).

### Track B — Conversation, Config & Reporting (owner: **Shagun** — confirmed)

| Order | Module | POA file | Depends on |
|---|---|---|---|
| **B1** | **Admin Console & Trigger Configuration (data model + CRUD) — SHIP FIRST** | `13_POA_Admin_Console_Trigger_Config.md` | A2 |
| B2 | Conversation Orchestrator | `08_POA_Conversation_Orchestrator.md` | A2, B3, B4, B5 |
| B3 | LLM Integration & Fallback Service | `09_POA_LLM_Integration_Fallback.md` | B2 |
| B4 | Claim Verification Service | `10_POA_Claim_Verification_Service.md` | booking-API mock (built) |
| B5 | Chatbot UI Integration (HS-103) | `11_POA_...HS103.md` | HS-103 mock (built) |
| B6 | Customer Response & Multi-turn Conversation Manager | `12_POA_...Conversation_Manager.md` | B2, A5, A8 |
| B7 | Audit, Reporting & Analytics | `14_POA_Audit_Reporting_Analytics.md` | A2, B1 |

Code lives under: `services/conversation/`.

**Two things Shagun must know reading this table:**

1. **B1 is day-one, ship-first — ahead of B2–B7.** Three of Prasad's eight
   modules (A5, A6, A8) wait on M13's config schema, and the master index
   puts M13 in **Phase 0** as a foundation. If B1 lands late, Prasad builds
   three modules against `generator/fixtures.py` defaults and then reworks
   them — the exact wasted-effort failure this document exists to prevent.
   B1's deliverable that unblocks Track A is small: the **config data model +
   migration + CRUD**. The admin *UI* can follow later, alongside B5.
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
| S1 Taxonomy & stations | Prasad | not started | |
| S2 Fee completeness | Prasad | not started | |
| S5 Load/SLA knobs | Prasad | not started | |
| A1 Platform skeleton | Prasad | not started | |
| A2 Event Store | Prasad | not started | |
| A3 Event Capture SDK | Prasad | not started | |
| A4 Ingestion API | Prasad | not started | |
| A5 Trigger Evaluation | Prasad | not started | waits on B1 |
| A6 Frequency/Precedence | Prasad | not started | waits on B1 |
| A7 Pending Queue/Scheduler | Prasad | not started | |
| A8 Human Handoff | Prasad | not started | waits on B1 |
| S3 Intent scenarios / scripted trees | Shagun | not started | |
| S4 PII redaction fixtures | Shagun | not started | |
| S6 Client-compat repository layer | Shagun | not started | |
| B1 Admin config model + CRUD | Shagun | not started | **ship first — unblocks A5/A6/A8** |
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
2. **Repo layout for service code.** No POA specifies one — `POA/16 §14`
   covers only `test_data/`/`generator/`/`mocks/`, and `POA/15` has no layout
   section. This document assumes `services/event_pipeline/`,
   `services/platform/`, `services/conversation/`. Agree on it before A1/B1
   start, and record the answer in `POA/15` so it has a single home.
3. **Branch protection on `main`** — required PR review, no direct pushes.
   Worth enabling once both branches exist, so a bad merge can't land without
   the other person seeing the diff.
