# HFB Proactive AI Chatbot — Test-Data Generator

A runnable, self-validating **synthetic test-dataset generator** for the HFB
Proactive AI Chatbot. It implements the strategy in
[`POA/16_Test_Dataset_Strategy.md`](POA/16_Test_Dataset_Strategy.md).

Because there is no real "customer-transaction-with-chatbot" data, this package
**generates it** — events, a reference world, config, LLM/claim fixtures and
reply scripts — all derived from **one seeded world** so everything stays
internally consistent. It ships with a pytest suite that proves the dataset
drives the correct outcomes **before the real M02–M14 services exist**.

> **v0.2 — SME business domain.** The dataset now also backs a *conversational*
> SME rental assistant, not just the proactive/verification pipeline. It adds
> business **companies**, negotiated **rate plans**, an **invoice** roll-up, a
> full booking **lifecycle** (upcoming/active/completed/cancelled), **van/LCV**
> classes, enriched vehicle & location attributes, per-location currency, and
> queryable **protection**, **extras** and **policy** catalogues. See
> [`POA/17_Mock_Dataset_Audit.md`](POA/17_Mock_Dataset_Audit.md) for the audit
> that drove these additions.

## Quick start

```bash
pip install -r requirements-dev.txt          # generator + services + test deps
python -m pytest                              # 100 tests, all green
python -m generator build --seed 42 --tier golden --out test_data
python -m generator build --seed 42 --tier volume --customers 1000 --out test_data
```

The generator is pure `pydantic + pyyaml`; the **services** (Track A/B, see below)
add `fastapi + pydantic-settings + prometheus-client`. `requirements-dev.txt`
installs everything.

## Layout

```
generator/        the generator (pure Python + pydantic)
  models.py       Pydantic CONTRACT models — single source of truth (share with services)
  world.py        WorldBuilder — seeded reference world (fleet/rates/availability/currency)
  catalogues.py   static reference: protection products, extras, policies, rate plans
  business.py     CompanyFactory + invoice roll-up (SME/corporate accounts)
  customers.py    CustomerFactory — customers + booking lifecycle (+ company/plan links)
  patterns.py     the 8 signal-pattern generators (flow nodes C–J)
  sessions.py     SessionSimulator — Tier-B volume driver
  scenarios.py    ScenarioComposer — 7 golden scenarios w/ pinned expectations
  intents.py      IntentScenarioComposer — 17 scripted conversation trees (inbound),
                  + ReplySource & Evaluator protocols (Phase-1 scripted / Phase-2 LLM)
  reference.py    executable spec of the trust-critical decisions (M05/M09/M10/M12)
  fixtures.py     default triggers + handoff routing rules (M13)
  pipeline.py     build() + writers (JSONL/JSON/YAML)
  cli.py          `python -m generator build ...`
mocks/            external systems, all reading the SAME world
  booking_api.py  rate/availability + claim verifier (M10) + forced-failure mode
  llm_provider.py deterministic LLM fixtures + timeout mode (M09)
  hs103.py        delivery + inbound replies (M11)
  support_queue.py handoff dispatch (M07)
tests/            pytest suite (contracts, world, patterns, golden, verification,
                  invariants, business, conversation intents, fees/disputes,
                  taxonomy/stations, load config, platform skeleton)
services/         service code (built on the generator's contract models)
  platform/       shared FastAPI/Celery template — create_app() wires logging,
                  correlation-id, Prometheus /metrics, OTel seam, health/readyz,
                  error handling, lazy Postgres/Redis/Celery factories (M15)
  event_pipeline/ Track A: event & trigger pipeline (A2–A8 land here)
```

### Generated files (`test_data/`)

```
world/    locations · vehicle_classes · rate_cards · availability
          protection_products · extras · policies            (v0.2 catalogues)
master/   customers · bookings · rate_plans                  (rate_plans always)
          companies · invoices                               (v0.2 business layer, volume tier)
config/   triggers · routing_rules · load_profile        (load_profile: S5 load/SLA targets)
scenarios/ + expected/   7 golden scenarios and their pinned outcomes
disputes/                5 hand-authored fee-dispute fixtures (S2)
events/volume/           bulk behavioural events
conversations/           17 scripted conversation trees (one per intent)
```

## The one idea that makes it work

**One seeded world backs both sides of every test.** The booking-API mock reads
its prices/availability from the same `World` that generated the events and the
LLM claims. So a *"wrong price"* test is airtight: the LLM fixture quotes £42.21,
the mock returns the world's true £52.21, and the verifier must correct/strip it
— asserted by `delivered_excludes`.

## Two tiers

| Tier | What | Assertions |
|------|------|-----------|
| **Golden** (`scenarios/`, `expected/`) | 7 hand-authored scenarios, one per decision branch | exact per-scenario outcomes |
| **Volume** (`events/volume/`, `master/`) | bulk data from documented distributions (`config.py`) | aggregate/invariant (≤cap, zero unverified claims) |

## Test coverage map (what each test proves)

| Test file | Proves |
|-----------|--------|
| `test_contracts.py` | generated data is schema-valid; malformed events rejected (extra/enum/missing) |
| `test_world_consistency.py` | world is deterministic per seed; rates/availability exist; sold-out keys exist |
| `test_signal_patterns.py` | each of the 8 signals reproduced & world-grounded; deferred I/J covered |
| `test_claim_verification.py` | M10: correct passes, wrong corrected, unverifiable stripped |
| `test_golden_scenarios.py` | every branch (W/AA/O/AE/AH) drives its pinned expected outcome |
| `test_invariants.py` | frequency cap never exceeded over volume; **no unverified claim ever delivered** |
| `test_business_entities.py` | v0.2 companies/plans/invoices/catalogues are schema-valid & referentially consistent; booking lifecycle exercised; totals derived not flat |
| `test_conversation_intents.py` | all 17 POA/16 §16.4 intents covered; conversation claims grounded in the world; wrong quotes differ from live data and are excluded; mid-conversation requirement changes override slots; the reply source is swappable (§16.6 Phase-2 seam) |
| `test_taxonomy_stations.py` | S1: 12-class taxonomy + airport/city/suburban stations + US/USD region present; one-way is domestic-only; golden prices unchanged |
| `test_fees_and_disputes.py` | S2: one-way/late-return/no-show/fuel fees generated & some disputed; late-return grace/day rule; 5 dispute fixtures internally consistent |
| `test_load_config.py` | S5: LoadProfile knobs match the §16.3 targets and are exposed to the load tier |
| `test_platform_skeleton.py` | A1: the shared template scaffolds a module with health/readyz, metrics, correlation-id and error handling wired in (POA/15 §7) |

## Services (Track A / Track B)

Service code lives in `services/` (repo layout ratified in
[`POA/15 §12`](POA/15_POA_Platform_Infra_Security_Observability.md)). Every module
is built from the shared `services.platform` template:

```python
from services.platform import create_app
app = create_app("event-pipeline")   # health, /metrics, logging, tracing, errors wired in
```

Run the Track A service and the local stack:

```bash
uvicorn services.event_pipeline.main:app --reload        # http://localhost:8000
#   GET /healthz  ·  /readyz  ·  /metrics  ·  /docs
docker compose up                                        # Postgres + Redis + service
```

Config is env-driven (`HFB_*`; copy `.env.example` → `.env`) — no secrets in code.
CI (`.github/workflows/ci.yml`) runs the pytest matrix and `ruff check services`.

## Extending

The scaffold runs on **pydantic + pyyaml + pytest** only. Layer in the optional
tools from `POA/16 §8` as the real project grows (Faker, numpy, Hypothesis,
respx, testcontainers, polyfactory) — see the commented block in
`requirements-dev.txt`.

> When the real services are built, import the models from `generator/models.py`
> (or move them to a shared package) so the generator and the services share one
> contract — then contract tests pass by construction.
