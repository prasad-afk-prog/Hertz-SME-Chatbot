"""build() — assemble the full Dataset, and write it to disk in the layout from
POA/16 §14. JSONL for events, JSON for world/master, YAML for config/scenarios.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .config import GenConfig
from .fixtures import default_routing_rules, default_triggers
from .models import Dataset
from .scenarios import ScenarioComposer
from .volume import VolumeSampler
from .world import WorldBuilder


def build(cfg: GenConfig, include_volume: bool = True) -> Dataset:
    world = WorldBuilder(cfg.seed).build()
    scenarios = ScenarioComposer(world).all()

    if include_volume:
        customers, bookings, _sessions, events = VolumeSampler(cfg, world).build()
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
    for sub in ("world", "master", "events/golden", "events/volume", "config", "scenarios", "expected"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    # world (always)
    _dump_json(out / "world/locations.json", [m.model_dump(mode="json") for m in ds.locations]); written.append(out / "world/locations.json")
    _dump_json(out / "world/vehicle_classes.json", [m.model_dump(mode="json") for m in ds.vehicle_classes]); written.append(out / "world/vehicle_classes.json")
    _dump_jsonl(out / "world/rate_cards.jsonl", ds.rate_cards); written.append(out / "world/rate_cards.jsonl")
    _dump_jsonl(out / "world/availability.jsonl", ds.availability); written.append(out / "world/availability.jsonl")

    # config (always)
    _dump_yaml(out / "config/triggers.yaml", [m.model_dump(mode="json") for m in ds.triggers]); written.append(out / "config/triggers.yaml")
    _dump_yaml(out / "config/routing_rules.yaml", [m.model_dump(mode="json") for m in ds.routing_rules]); written.append(out / "config/routing_rules.yaml")

    # golden tier
    if tier in ("all", "golden"):
        for sc in ds.scenarios:
            p = out / f"scenarios/{sc.scenario_id}.json"
            _dump_json(p, sc.model_dump(mode="json")); written.append(p)
            e = out / f"expected/{sc.scenario_id}.yaml"
            _dump_yaml(e, sc.expected.model_dump(mode="json")); written.append(e)

    # volume tier
    if tier in ("all", "volume"):
        _dump_jsonl(out / "master/customers.jsonl", ds.customers); written.append(out / "master/customers.jsonl")
        _dump_jsonl(out / "master/bookings.jsonl", ds.bookings); written.append(out / "master/bookings.jsonl")
        _dump_jsonl(out / "events/volume/events.jsonl", ds.events); written.append(out / "events/volume/events.jsonl")

    return written
