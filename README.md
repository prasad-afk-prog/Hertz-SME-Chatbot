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
pip install -r requirements-dev.txt          # pydantic, pyyaml, pytest, anthropic
python -m pytest                              # 282 tests, all green
python -m generator build --seed 42 --tier golden --out test_data
python -m generator build --seed 42 --tier volume --customers 1000 --out test_data
```

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
  pii.py          PII redaction fixtures + the PII_FIELDS data dictionary (S4)
  repository.py   ReferenceRepository seam + lenient DTO for future client data (S6)
  field_map.yaml  client vocabulary -> canonical (hand-authored; NOT generated)
  reference.py    executable spec of the trust-critical decisions
                  (M05/M08-09 redaction/M09/M10/M12)
  fixtures.py     default triggers + handoff routing rules (M13)
  pipeline.py     build() + writers (JSONL/JSON/YAML)
  cli.py          `python -m generator build ...`
mocks/            external systems, all reading the SAME world
  booking_api.py  rate/availability + claim verifier (M10) + forced-failure mode
  llm_provider.py deterministic LLM fixtures + timeout mode (M09)
  hs103.py        delivery + inbound replies (M11)
  support_queue.py handoff dispatch (M07)
services/         the real services
  common/resilience.py               shared circuit breaker + TTL cache (injected clock)
  conversation/claim_verification/   M10 - detection, booking-API edge, resolution
  conversation/llm/                  M09 - Anthropic provider, confidence gate, fallbacks, budgets
  conversation/delivery/             M11 - HS-103 delivery, deep links, correlation, receipts
  conversation/orchestrator/         M08 - context, personalisation, prompts, state, pipeline
tests/            pytest suite (contracts, world, patterns, golden, verification,
                  invariants, business, conversation intents, PII, repository, M10)
```

### Generated files (`test_data/`)

```
world/    locations · vehicle_classes · rate_cards · availability
          protection_products · extras · policies            (v0.2 catalogues)
master/   customers · bookings · rate_plans                  (rate_plans always)
          companies · invoices                               (v0.2 business layer, volume tier)
config/   triggers · routing_rules
scenarios/ + expected/   7 golden scenarios and their pinned outcomes
events/volume/           bulk behavioural events
conversations/           17 scripted conversation trees (one per intent)
fixtures/                pii_redaction.json — 13 PII redaction fixtures
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
| `test_orchestrator_service.py` | **M08 service**: fire -> delivered end to end; personalisation changes language and tone; **the context allow-list is disjoint from every PII-marked field** and no S4 fixture value reaches a prompt; injected customer text stays inside the fence and an injected fake price is still killed by M10; all five failure points still reach the customer safely; delivery failure rolls the reservation back |
| `test_provider_budget_tolerance.py` | **M09 §5.1/§5.6 + M10 §5.3/§5.4**: the Anthropic adapter's request shape (model, structured claim output, cached system prompt), SDK error mapping, refusal handling; token/spend budgets; the HTTP booking client with bearer/API-key/HMAC auth and credentials kept out of `repr`; all five tolerance modes |
| `test_delivery_service.py` | **M11 service**: proactive delivery, presence gating, anti-nag, deep-link payloads, reply correlation across concurrent conversations, idempotent webhooks, retry, and receipts feeding M14 |
| `test_llm_fallback_service.py` | **M09 service**: the confidence truth table; provider timeout/outage/low-confidence/refusal/off-scope all yield a localised fallback, never an error; **no fallback template asserts a price or availability**; retries bounded and counted; provider switchable by config alone |
| `test_claim_verification_service.py` | **M10 service**: correct claims pass, wrong are corrected, outage/timeout strip the claim while still delivering; the red-team test proves no unverified claim survives any branch; cache keyed on date and never caching failures; breaker opens on outage but not on a bad lookup |
| `test_repository_compat.py` | S6: the repository agrees with the world on **every** rate/availability key; an unrelated implementation satisfies the same protocol; coercion renames/maps/drops **and reports**; the strict models are still strict; a typo'd enum target in `field_map.yaml` fails at load |
| `test_pii_redaction.py` | S4: synthetic PII is provably fake (cards fail Luhn, emails RFC 2606, phones Ofcom drama block, IPs RFC 5737); spans are exact; redaction removes all PII **and nothing else**; the PII field marking still matches the models |
| `test_conversation_intents.py` | all 17 POA/16 §16.4 intents covered; conversation claims grounded in the world; wrong quotes differ from live data and are excluded; mid-conversation requirement changes override slots; the reply source is swappable (§16.6 Phase-2 seam) |

## Extending

The scaffold runs on **pydantic + pyyaml + pytest** only. Layer in the optional
tools from `POA/16 §8` as the real project grows (Faker, numpy, Hypothesis,
respx, testcontainers, polyfactory) — see the commented block in
`requirements-dev.txt`.

> When the real services are built, import the models from `generator/models.py`
> (or move them to a shared package) so the generator and the services share one
> contract — then contract tests pass by construction.
