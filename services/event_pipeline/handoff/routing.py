"""Routing-rule evaluation (POA/07 §3.1) — deliberately THIN.

RoutingRule.match/.route/.sla are bare dicts today (M13 may tighten them, POA/18
§8 A8 caveat), so this reads them as dicts and doesn't build types on top. Ordered
rules, first match wins; a rule matches when every key in its `match` dict equals
the handoff context. The default `catch_all` rule (match={}) matches everything.
"""
from __future__ import annotations

from generator.models import RoutingRule


def route_for(context: dict, rules: list[RoutingRule]) -> RoutingRule | None:
    """First rule whose `match` dict is satisfied by `context`; None if none match."""
    for rule in rules:
        if all(context.get(key) == value for key, value in rule.match.items()):
            return rule
    return None


def context_of(language: str, customer_type: str | None, priority: str | None) -> dict:
    """Build the match context from a handoff, omitting unset dimensions so they
    don't accidentally force a rule to miss."""
    ctx: dict = {"language": language}
    if customer_type is not None:
        ctx["customer_type"] = customer_type
    if priority is not None:
        ctx["priority"] = priority
    return ctx
