"""Sandboxed rule DSL (POA/04 §3.2, §8) — field/op/value predicates only, never
`eval`. A condition is ``{"field": "context.step", "op": "in", "value": [...]}``;
the field is a dotted attribute path over the Event, resolved by getattr.
"""
from __future__ import annotations

from typing import Any

from generator.models import Event, TriggerConfig

_OPS = {"eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "exists"}


def _get(event: Event, path: str) -> Any:
    obj: Any = event
    for part in path.split("."):
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


def eval_condition(event: Event, cond: dict) -> bool:
    try:
        field, op = cond["field"], cond["op"]
    except KeyError as exc:
        raise ValueError(f"condition missing {exc}") from exc
    if op not in _OPS:
        raise ValueError(f"unsupported op {op!r}")
    value = cond.get("value")
    actual = _get(event, field)
    if op == "exists":
        return actual is not None
    if actual is None:
        return False
    match op:
        case "eq":
            return actual == value
        case "ne":
            return actual != value
        case "in":
            return actual in value
        case "not_in":
            return actual not in value
        case "gt":
            return actual > value
        case "gte":
            return actual >= value
        case "lt":
            return actual < value
        case "lte":
            return actual <= value
    return False


def matches(event: Event, rule: TriggerConfig) -> bool:
    if rule.match.signal_type != event.signal_type:
        return False
    return all(eval_condition(event, c) for c in rule.match.conditions)


def matching_rules(event: Event, rules: list[TriggerConfig]) -> list[TriggerConfig]:
    return [r for r in rules if r.enabled and matches(event, r)]
