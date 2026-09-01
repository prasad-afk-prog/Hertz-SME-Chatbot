"""Future-client-compatibility layer (S6) — POA/16 §16 item 6.

The goal in the spec is that the eight components in §16.7 stay **swappable for
production data with minimal code change**. This module supplies the three
named artifacts — a repository interface, a `field_map.yaml`, and a lenient DTO
— but only for the reference-data components. The rest were already swappable,
and saying so precisely is half the deliverable:

| # | §16.7 component | Swap point | Added by |
|---|-----------------|-----------|----------|
| 1 | Vehicle taxonomy | `ReferenceRepository.vehicle_classes` | **S6** |
| 2 | Station / location dataset | `ReferenceRepository.locations` | **S6** |
| 3 | Pricing / fee rules | `ReferenceRepository.rate` / `.deposit` / `.nominal_daily_rate` | **S6** |
| 4 | Funnel / conversation distribution | `GenConfig` — pass a different instance | already |
| 5 | Load / traffic targets | `GenConfig` sizing fields | already |
| 6 | PII classification rules | `pii.PII_FIELDS` — a module-level dict | already |
| 7 | Conversation scenarios | `intents.ReplySource` protocol | already (S3) |
| 8 | Expected chatbot outcomes | `intents.Evaluator` protocol | already (S3) |

So S6 closes 1–3 and documents 4–8. A service written against
`ReferenceRepository` does not know whether its rates come from the seeded
synthetic world or a live client feed.

**The interface was derived from real call sites, not invented** — it is exactly
the public surface of `World` that `generator/`, `mocks/` and `tests/` actually
reach for today. Adding a method nobody calls would be speculative; omitting one
that is called would break the swap.

### On leniency

`coerce()` is a **boundary-only** entry point for messy external records. The
contract models keep `extra="forbid"` and stay strict — `test_contracts.py`
proves malformed input is still rejected, and S6 does not relax that. The
lenient step happens *before* validation: rename fields, map values, drop what
the canonical schema has no home for, then hand a clean dict to the strict
model.

Crucially, leniency is **not silence**. Every rename, drop and unmapped value
comes back in a `CoercionReport`. A dropped field is potential data loss; it has
to be visible to the caller, not buried in a log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, TypeVar, get_args, runtime_checkable

import yaml
from pydantic import BaseModel

from .models import (
    Availability,
    Extra,
    Location,
    Policy,
    ProtectionProduct,
    RateCard,
    VehicleClass,
)
from .world import World

DEFAULT_FIELD_MAP = Path(__file__).with_name("field_map.yaml")

M = TypeVar("M", bound=BaseModel)


# --------------------------------------------------------------------------- #
# 1. The repository seam (§16.7 components 1-3)
# --------------------------------------------------------------------------- #
@runtime_checkable
class ReferenceRepository(Protocol):
    """Read access to reference data, independent of where it came from.

    NOTE: `isinstance` against a runtime_checkable Protocol only checks that the
    names exist — it does not check signatures. The real guarantee comes from
    `test_repository_compat.py`, which asserts a repository agrees with the
    world on every rate and availability key.
    """

    # -- collections --
    @property
    def locations(self) -> list[Location]: ...
    @property
    def vehicle_classes(self) -> list[VehicleClass]: ...
    @property
    def rate_cards(self) -> list[RateCard]: ...
    @property
    def availability(self) -> list[Availability]: ...
    @property
    def protection_products(self) -> list[ProtectionProduct]: ...
    @property
    def extras(self) -> list[Extra]: ...
    @property
    def policies(self) -> list[Policy]: ...

    # -- identifiers --
    @property
    def location_ids(self) -> list[str]: ...
    @property
    def vehicle_codes(self) -> list[str]: ...

    # -- calendar window --
    @property
    def start(self) -> date: ...
    @property
    def end(self) -> date: ...
    @property
    def days(self) -> int: ...

    # -- lookups --
    def rate(self, location_id: str, vehicle_class: str, on: date) -> Decimal: ...
    def availability_count(self, location_id: str, vehicle_class: str, on: date) -> int: ...
    def has(self, location_id: str, vehicle_class: str, on: date) -> bool: ...
    def currency(self, location_id: str) -> str: ...
    def deposit(self, vehicle_class: str) -> Decimal: ...
    def nominal_daily_rate(self, location_id: str, vehicle_class: str) -> Decimal: ...


class GeneratedRepository:
    """Phase-1 implementation: the seeded synthetic world.

    Deliberately a thin delegation rather than a copy — if it reshaped the data
    it could drift from `World`, and the whole value of the seam is that both
    sides of a verification test see identical numbers.
    """

    def __init__(self, world: World) -> None:
        self._w = world

    # collections
    @property
    def locations(self) -> list[Location]: return self._w.locations
    @property
    def vehicle_classes(self) -> list[VehicleClass]: return self._w.vehicle_classes
    @property
    def rate_cards(self) -> list[RateCard]: return self._w.rate_cards
    @property
    def availability(self) -> list[Availability]: return self._w.availability
    @property
    def protection_products(self) -> list[ProtectionProduct]: return self._w.protection_products
    @property
    def extras(self) -> list[Extra]: return self._w.extras
    @property
    def policies(self) -> list[Policy]: return self._w.policies

    # identifiers
    @property
    def location_ids(self) -> list[str]: return self._w.location_ids
    @property
    def vehicle_codes(self) -> list[str]: return self._w.vehicle_codes

    # calendar
    @property
    def start(self) -> date: return self._w.start
    @property
    def end(self) -> date: return self._w.end
    @property
    def days(self) -> int: return self._w.days

    # lookups
    def rate(self, location_id: str, vehicle_class: str, on: date) -> Decimal:
        return self._w.rate(location_id, vehicle_class, on)

    def availability_count(self, location_id: str, vehicle_class: str, on: date) -> int:
        return self._w.availability_count(location_id, vehicle_class, on)

    def has(self, location_id: str, vehicle_class: str, on: date) -> bool:
        return self._w.has(location_id, vehicle_class, on)

    def currency(self, location_id: str) -> str:
        return self._w.currency(location_id)

    def deposit(self, vehicle_class: str) -> Decimal:
        return self._w.deposit(vehicle_class)

    def nominal_daily_rate(self, location_id: str, vehicle_class: str) -> Decimal:
        return self._w.nominal_daily_rate(location_id, vehicle_class)


# --------------------------------------------------------------------------- #
# 2. field_map.yaml — client vocabulary -> canonical vocabulary
# --------------------------------------------------------------------------- #
@dataclass
class ModelMap:
    """How one client record shape maps onto one canonical model."""
    fields: dict[str, str] = field(default_factory=dict)          # client -> canonical
    values: dict[str, dict[str, str]] = field(default_factory=dict)  # canonical field -> {client value -> canonical}
    drop: list[str] = field(default_factory=list)                 # known-irrelevant, dropped quietly


@dataclass
class FieldMap:
    """The whole mapping file, keyed by canonical model name."""
    models: dict[str, ModelMap] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_FIELD_MAP) -> FieldMap:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        models = {
            name: ModelMap(
                fields=spec.get("fields", {}) or {},
                values=spec.get("values", {}) or {},
                drop=spec.get("drop", []) or [],
            )
            for name, spec in (raw.get("models") or {}).items()
        }
        return cls(models=models)

    def for_model(self, model_name: str) -> ModelMap:
        return self.models.get(model_name, ModelMap())


# --------------------------------------------------------------------------- #
# 3. The lenient DTO step
# --------------------------------------------------------------------------- #
@dataclass
class CoercionReport:
    """What the lenient step did. Returned, never merely logged.

    `dropped` is the one to watch: it is the list of things the client sent that
    the canonical schema has nowhere to put. That is potential data loss, and a
    caller that ignores it is choosing to lose data knowingly.
    """
    model: str
    renamed: dict[str, str] = field(default_factory=dict)
    mapped_values: dict[str, tuple[str, str]] = field(default_factory=dict)
    dropped: dict[str, Any] = field(default_factory=dict)
    dropped_by_config: dict[str, Any] = field(default_factory=dict)

    @property
    def lossless(self) -> bool:
        """True when nothing was discarded except fields the map explicitly drops."""
        return not self.dropped


class StrictCoercionError(ValueError):
    """Raised in strict mode when a client record carries unmappable fields."""


def coerce(
    model_cls: type[M],
    raw: dict[str, Any],
    field_map: FieldMap | None = None,
    *,
    strict: bool = False,
) -> tuple[M, CoercionReport]:
    """Turn one messy external record into a validated canonical model.

    Boundary-only: the strict `extra="forbid"` models are unchanged, and a raw
    client record would fail them outright — which is exactly why this step
    exists. Order matters: rename, then map values, then drop, then validate.

    `strict=True` raises instead of dropping, for callers who would rather fail
    an import than silently lose a field.
    """
    name = model_cls.__name__
    mm = (field_map or FieldMap()).for_model(name)
    report = CoercionReport(model=name)
    known = set(model_cls.model_fields)

    clean: dict[str, Any] = {}
    for key, value in raw.items():
        if key in mm.drop:
            report.dropped_by_config[key] = value
            continue

        canonical = mm.fields.get(key, key)
        if canonical != key:
            report.renamed[key] = canonical

        if canonical not in known:
            report.dropped[key] = value
            continue

        if canonical in mm.values and isinstance(value, str):
            mapped = mm.values[canonical].get(value)
            if mapped is not None:
                report.mapped_values[canonical] = (value, mapped)
                value = mapped

        clean[canonical] = value

    if strict and report.dropped:
        raise StrictCoercionError(
            f"{name}: no canonical home for {sorted(report.dropped)} "
            "— add them to field_map.yaml (fields:) or list them under drop:"
        )

    return model_cls(**clean), report


def coerce_many(
    model_cls: type[M],
    rows: list[dict[str, Any]],
    field_map: FieldMap | None = None,
    *,
    strict: bool = False,
) -> tuple[list[M], list[CoercionReport]]:
    out, reports = [], []
    for row in rows:
        model, rep = coerce(model_cls, row, field_map, strict=strict)
        out.append(model)
        reports.append(rep)
    return out, reports


# --------------------------------------------------------------------------- #
# 4. Map validation — a typo'd enum target must fail loudly, not at runtime
# --------------------------------------------------------------------------- #
def _enum_of(annotation: Any) -> type[Enum] | None:
    """The Enum inside an annotation, unwrapping `X | None`.

    Most enum-typed fields in the contract models are optional (`Transmission |
    None`), so a naive `issubclass(annotation, Enum)` matches none of them and
    the value-map check silently validates nothing.
    """
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation
    for arg in get_args(annotation):
        if isinstance(arg, type) and issubclass(arg, Enum):
            return arg
    return None


def validate_field_map(field_map: FieldMap) -> list[str]:
    """Return a list of problems; empty means the map is coherent.

    Checks that every canonical field named actually exists on its model, and
    that every value-map target is a real member of that field's enum. Without
    this a YAML typo surfaces as a validation error on live client data.
    """
    import generator.models as models_mod

    problems: list[str] = []
    for model_name, mm in field_map.models.items():
        model = getattr(models_mod, model_name, None)
        if model is None or not issubclass(model, BaseModel):
            problems.append(f"{model_name}: not a contract model")
            continue

        known = set(model.model_fields)
        for client_field, canonical in mm.fields.items():
            if canonical not in known:
                problems.append(
                    f"{model_name}.{canonical} (mapped from {client_field!r}) is not a field"
                )

        for canonical, mapping in mm.values.items():
            if canonical not in known:
                problems.append(f"{model_name}.{canonical}: value map for an unknown field")
                continue
            enum_cls = _enum_of(model.model_fields[canonical].annotation)
            if enum_cls is not None:
                allowed = {member.value for member in enum_cls}
                for client_value, target in mapping.items():
                    if target not in allowed:
                        problems.append(
                            f"{model_name}.{canonical}: {client_value!r} -> {target!r} "
                            f"is not a valid {enum_cls.__name__}"
                        )
    return problems
