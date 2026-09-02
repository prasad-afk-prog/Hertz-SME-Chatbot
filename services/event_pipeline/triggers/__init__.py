"""A5 Trigger Evaluation Engine (POA/04 / M04) — the brain.

    from services.event_pipeline.triggers import TriggerEvaluator

Consumes events:in, matches admin-configured rules (sandboxed DSL), and routes:
in-session -> A6 arbitrate -> fire; deferred -> queue; (handoff seam).
"""
from __future__ import annotations

from .consumer import RedisTriggerConsumer
from .dsl import eval_condition, matches, matching_rules
from .evaluator import Decision, EvaluationResult, TriggerEvaluator, parse_stream_fields
from .sinks import (
    DeferredItem,
    InMemoryDeferredSink,
    InMemoryFireSink,
    InMemoryIdempotencyGuard,
    InMemorySuppressionSink,
    StaticRuleSource,
)

__all__ = [
    "TriggerEvaluator",
    "EvaluationResult",
    "Decision",
    "parse_stream_fields",
    "RedisTriggerConsumer",
    "matching_rules",
    "matches",
    "eval_condition",
    "DeferredItem",
    "StaticRuleSource",
    "InMemoryFireSink",
    "InMemoryDeferredSink",
    "InMemorySuppressionSink",
    "InMemoryIdempotencyGuard",
]
