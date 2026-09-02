"""build() — assemble the full Dataset, and write it to disk in the layout from
POA/16 §14. JSONL for events, JSON for world/master, YAML for config/scenarios.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import yaml

from .catalogues import rate_plans as catalogue_rate_plans
from .config import GenConfig
from .fixtures import default_routing_rules, default_triggers
from .intents import IntentScenarioComposer
from .models import Dataset
from .pii import RedactionFixtureBuilder
from .scenarios import FeeDisputeComposer, ScenarioComposer
from .volume import VolumeSampler
from .world import WorldBuilder


def build(cfg: GenConfig, include_volume: bool = True) -> Dataset:
    world = WorldBuilder(cfg.seed).build()
    scenarios = ScenarioComposer(world).all()
    conversations = IntentScenarioComposer(world).all()
    redaction_fixtures = RedactionFixtureBuilder().all()
    fee_disputes = FeeDisputeComposer(world).all()   # S2 — POA/16 §16.1
    rate_plans = catalogue_rate_plans()

    companies: list = []
    invoices: list = []
    if include_volume:
        sampler = VolumeSampler(cfg, world)
        customers, bookings, _sessions, events = sampler.build()
        companies, rate_plans, invoices = sampler.companies, sampler.rate_plans, sampler.invoices
    else:
        customers, bookings, events = [], [], []

    return Dataset(
        seed=cfg.seed,
        locations=world.locations,
        vehicle_classes=world.vehicle_classes,
        rate_cards=world.rate_cards,
        availability=world.availability,
        customers=customers,
        bookings=bookings,
        events=events,
        triggers=default_triggers(),
        routing_rules=default_routing_rules(),
        scenarios=scenarios,
        companies=companies,
        rate_plans=rate_plans,
        invoices=invoices,
        protection_products=world.protection_products,
        extras=world.extras,
        policies=world.policies,
        conversations=conversations,
        redaction_fixtures=redaction_fixtures,
        fee_disputes=fee_disputes,
        load_profile=asdict(cfg.load),
    )


def _dump_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _dump_jsonl(path: Path, models) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for m in models:
            fh.write(json.dumps(m.model_dump(mode="json")) + "\n")


def _dump_yaml(path: Path, obj) -> None:
    path.write_text(yaml.safe_dump(obj, sort_keys=False, allow_unicode=True), encoding="utf-8")


def write(ds: Dataset, out: Path, tier: str = "all") -> list[Path]:
    """Write the dataset; returns the paths written."""
    written: list[Path] = []
    for sub in ("world", "master", "events/golden", "events/volume", "config", "scenarios", "expected",
                "conversations", "fixtures", "disputes"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    # world (always)
    _dump_json(out / "world/locations.json", [m.model_dump(mode="json") for m in ds.locations]); written.append(out / "world/locations.json")
    _dump_json(out / "world/vehicle_classes.json", [m.model_dump(mode="json") for m in ds.vehicle_classes]); written.append(out / "world/vehicle_classes.json")
    _dump_jsonl(out / "world/rate_cards.jsonl", ds.rate_cards); written.append(out / "world/rate_cards.jsonl")
    _dump_jsonl(out / "world/availability.jsonl", ds.availability); written.append(out / "world/availability.jsonl")
    # catalogues (always) — protection, extras, policies
    _dump_json(out / "world/protection_products.json", [m.model_dump(mode="json") for m in ds.protection_products]); written.append(out / "world/protection_products.json")
    _dump_json(out / "world/extras.json", [m.model_dump(mode="json") for m in ds.extras]); written.append(out / "world/extras.json")
    _dump_json(out / "world/policies.json", [m.model_dump(mode="json") for m in ds.policies]); written.append(out / "world/policies.json")

    # config (always)
    _dump_yaml(out / "config/triggers.yaml", [m.model_dump(mode="json") for m in ds.triggers]); written.append(out / "config/triggers.yaml")
    _dump_yaml(out / "config/routing_rules.yaml", [m.model_dump(mode="json") for m in ds.routing_rules]); written.append(out / "config/routing_rules.yaml")
    # load/SLA profile (always) — S5 (POA/16 §16.3) targets for the soak/load tier
    _dump_yaml(out / "config/load_profile.yaml", ds.load_profile); written.append(out / "config/load_profile.yaml")
    # rate plans (always) — small negotiated-plan catalogue
    _dump_jsonl(out / "master/rate_plans.jsonl", ds.rate_plans); written.append(out / "master/rate_plans.jsonl")

    # golden tier
    if tier in ("all", "golden"):
        for sc in ds.scenarios:
            p = out / f"scenarios/{sc.scenario_id}.json"
            _dump_json(p, sc.model_dump(mode="json")); written.append(p)
            e = out / f"expected/{sc.scenario_id}.yaml"
            _dump_yaml(e, sc.expected.model_dump(mode="json")); written.append(e)
        # scripted conversation trees (S3 — POA/16 §16.4/§16.6)
        for cv in ds.conversations:
            p = out / f"conversations/{cv.conversation_id}.json"
            _dump_json(p, cv.model_dump(mode="json")); written.append(p)
            e = out / f"expected/{cv.conversation_id}.yaml"
            _dump_yaml(e, cv.expected.model_dump(mode="json")); written.append(e)
        # PII redaction fixtures (S4 - POA/16 16.5)
        pf = out / "fixtures/pii_redaction.json"
        _dump_json(pf, [m.model_dump(mode="json") for m in ds.redaction_fixtures])
        written.append(pf)
        # fee-dispute fixtures (S2 — POA/16 §16.1)
        for fd in ds.fee_disputes:
            p = out / f"disputes/{fd.dispute_id}.json"
            _dump_json(p, fd.model_dump(mode="json")); written.append(p)

    # volume tier
    if tier in ("all", "volume"):
        _dump_jsonl(out / "master/customers.jsonl", ds.customers); written.append(out / "master/customers.jsonl")
        _dump_jsonl(out / "master/bookings.jsonl", ds.bookings); written.append(out / "master/bookings.jsonl")
        _dump_jsonl(out / "master/companies.jsonl", ds.companies); written.append(out / "master/companies.jsonl")
        _dump_jsonl(out / "master/invoices.jsonl", ds.invoices); written.append(out / "master/invoices.jsonl")
        _dump_jsonl(out / "events/volume/events.jsonl", ds.events); written.append(out / "events/volume/events.jsonl")

    return written
