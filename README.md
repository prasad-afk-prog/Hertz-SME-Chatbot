# HFB Proactive AI Chatbot — Test-Data Generator

A runnable, self-validating **synthetic test-dataset generator** for the HFB
Proactive AI Chatbot. It implements the strategy in
[`POA/16_Test_Dataset_Strategy.md`](POA/16_Test_Dataset_Strategy.md).

Because there is no real "customer-transaction-with-chatbot" data, this package
**generates it** — events, a reference world, config, LLM/claim fixtures and
reply scripts — all derived from **one seeded world** so everything stays
internally consistent. It ships with a pytest suite that proves the dataset
drives the correct outcomes **before the real M02–M14 services exist**.

## Quick start

```bash
pip install -r requirements-dev.txt          # pydantic, pyyaml, pytest
python -m pytest                              # 33 tests, all green
python -m generator build --seed 42 --tier golden --out test_data
python -m generator build --seed 42 --tier volume --customers 1000 --out test_data
```

## Layout

```
generator/        the generator (pure Python + pydantic)
  models.py       Pydantic CONTRACT models — single source of truth (share with services)
  world.py        WorldBuilder — seeded reference world (fleet/rates/availability)
  customers.py    CustomerFactory — customers + booking history (+ dormant cohort)
  patterns.py     the 8 signal-pattern generators (flow nodes C–J)
  sessions.py     SessionSimulator — Tier-B volume driver
  scenarios.py    ScenarioComposer — 7 golden scenarios w/ pinned expectations
  reference.py    executable spec of the trust-critical decisions (M05/M09/M10/M12)
  fixtures.py     default triggers + handoff routing rules (M13)
  pipeline.py     build() + writers (JSONL/JSON/YAML)
  cli.py          `python -m generator build ...`
mocks/            external systems, all reading the SAME world
  booking_api.py  rate/availability + claim verifier (M10) + forced-failure mode
  llm_provider.py deterministic LLM fixtures + timeout mode (M09)
  hs103.py        delivery + inbound replies (M11)
  support_queue.py handoff dispatch (M07)
tests/            pytest suite (contracts, world, patterns, golden, verification, invariants)
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

## Extending

The scaffold runs on **pydantic + pyyaml + pytest** only. Layer in the optional
tools from `POA/16 §8` as the real project grows (Faker, numpy, Hypothesis,
respx, testcontainers, polyfactory) — see the commented block in
`requirements-dev.txt`.

> When the real services are built, import the models from `generator/models.py`
> (or move them to a shared package) so the generator and the services share one
> contract — then contract tests pass by construction.
