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
5. **Engagement reservation confirm/rollback** (B2 → A6) — **added 2026-09-01,
   and the one that needs agreeing soonest.** A fired trigger consumes one of
   the customer's capped engagements. M08 must confirm that reservation on
   successful delivery and **roll it back** when delivery fails, or the customer
   silently loses an engagement they never received and the cap tightens over
   time. M08 ships a `ReservationClient` protocol
   (`confirm(reservation_id)` / `rollback(reservation_id, reason)`) with an
   in-memory implementation. **The real shape is unagreed** — Prasad owns M05,
   so this needs ten minutes between the two of us: who mints the reservation
   id, whether rollback is idempotent, and what happens if the confirm call
   itself fails.

Whoever needs a contract change adds it to `generator/models.py`, runs
`pytest`, and says so explicitly at day-end. Don't assume the other person
spots it in the diff.

## 5b. OPEN — everything waiting on Prasad, in one place

*Added 2026-09-02. These have accumulated across six Track-B modules and have
been raised piecemeal in day-end notes without landing. Collected here because
they are answerable in one sitting, and scattered mentions have not worked.*

**Nothing below is urgent for Prasad's own work. All of it blocks Shagun's.**

| # | Question | Blocks | Cost of guessing |
|---|---|---|---|
| 1 | **Confirm the `services/` layout.** Six modules now live under `services/conversation/*` and `services/admin/config/` on the assumption in §8.2. | Nothing yet — but every new module raises the cost | A rename across six modules and ~320 tests |
| 2 | **Engagement reservation contract** (§5 item 5). Who mints the reservation id? Is `rollback` idempotent? What if `confirm` itself fails? | M08 ships a stub; M12 will need the real one | A customer silently loses a capped engagement they never received |
| 3 | **Config hot-reload consumer contract** (§5 item 6). M13 publishes `(entity, entity_id, version, enabled)`. Do M04/M05/M06/M07 want that, or a payload? | M13 task 4 | Config/runtime drift — POA/13 §8's second risk |
| 4 | **Who owns the rule DSL validator?** `TriggerMatch.conditions` is untyped `list[dict]`. POA/13 §8 says *one* validator shared with M04. M13 ships a named seam and validates nothing inside it. | M13 tasks 2 & 7 | Two validators that disagree — the exact divergence §8 warns about |
| 5 | **M04 and M07 message contracts** — even draft shapes would do. | All of POA/12 | Two more guessed cross-track contracts |

**Also worth ten minutes, not blocking:**

- `anthropic>=0.86` is now in `requirements.txt` (lazily imported, so nothing
  else breaks) — flagged per §4.
- Sprint-0 S1, S2 and S5 are still open on Track A.

**And one for product, not Prasad:** POA/09 §10.4 and POA/08 §10.3 are the same
question. The fallback copy (4 languages) and the prompt guardrails are both
**engineer-written placeholder text**. They are safe and claim-free, but nobody
in marketing or a native speaker has seen them, and they should not face
customers as-is.

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
| A5 Trigger Evaluation | Prasad | not started | |
| A6 Frequency/Precedence | Prasad | not started | |
| A7 Pending Queue/Scheduler | Prasad | not started | |
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
2. **Repo layout for service code.** No POA specifies one — `POA/16 §14`
   covers only `test_data/`/`generator/`/`mocks/`, and `POA/15` has no layout
   section. This document assumes `services/event_pipeline/`,
   `services/platform/`, `services/conversation/`. Agree on it before A1/B1
   start, and record the answer in `POA/15` so it has a single home.
3. **Branch protection on `main`** — required PR review, no direct pushes.
   Worth enabling once both branches exist, so a bad merge can't land without
   the other person seeing the diff.
