"""TriggerEvaluator (A5 / M04) — the brain (POA/04 §3.2).

Per event: idempotency guard -> match active rules -> node-N routing:
  * deferred matches   -> DeferredSink (M06/A7)
  * in-session matches -> A6.reserve (cap + precedence); approved -> FireSink
                          (M08/B2), suppressed -> SuppressionSink (Z1/M14)
  * no match           -> dropped (logged for analytics)

The handoff branch (events fed back from M12, POA/04 §10.3) is left as a seam —
M07/M12 don't exist yet and its representation is an open question.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from generator.fixtures import default_triggers
from generator.models import Event, EventContext, FireMessage, MatchCandidate, TriggerType

from ..frequency import FrequencyPrecedenceEngine
from .dsl import matching_rules
from .sinks import (
    DeferredItem,
    DeferredSink,
    FireSink,
    IdempotencyGuard,
    InMemoryDeferredSink,
    InMemoryFireSink,
    InMemoryIdempotencyGuard,
    InMemorySuppressionSink,
    RuleSource,
    StaticRuleSource,
    SuppressionSink,
)


class Decision(str, Enum):
    fired = "fired"
    suppressed = "suppressed"
    deferred = "deferred"
    dropped = "dropped"
    duplicate = "duplicate"


@dataclass
class EvaluationResult:
    event_id: str
    status: Decision
    matched: list[str] = field(default_factory=list)      # matched trigger ids
    deferred: list[str] = field(default_factory=list)     # trigger ids enqueued
    fire: FireMessage | None = None                       # in-session fire (if approved)
    suppressed: bool = False                              # in-session suppressed by A6


def parse_stream_fields(fields: dict) -> Event:
    """Reconstruct an Event from the relay's ``events:in`` stream fields."""
    return Event(
        event_id=fields["event_id"],
        customer_id=fields["customer_id"],
        session_id=fields["session_id"],
        signal_type=fields["signal_type"],
        occurred_at=fields["occurred_at"],
        context=EventContext(**json.loads(fields.get("context") or "{}")),
    )


class TriggerEvaluator:
    def __init__(
        self,
        engine: FrequencyPrecedenceEngine,
        *,
        rule_source: RuleSource | None = None,
        fire_sink: FireSink | None = None,
        deferred_sink: DeferredSink | None = None,
        suppression_sink: SuppressionSink | None = None,
        idempotency: IdempotencyGuard | None = None,
    ) -> None:
        self.engine = engine
        self.rules = rule_source or StaticRuleSource(default_triggers())
        self.fire_sink = fire_sink or InMemoryFireSink()
        self.deferred_sink = deferred_sink or InMemoryDeferredSink()
        self.suppression_sink = suppression_sink or InMemorySuppressionSink()
        self.idempotency = idempotency or InMemoryIdempotencyGuard()

    def evaluate(self, event: Event, now: datetime | None = None) -> EvaluationResult:
        if self.idempotency.seen(event.event_id):
            return EvaluationResult(event.event_id, Decision.duplicate)

        now = now or event.occurred_at
        matched = matching_rules(event, self.rules.active())
        if not matched:
            return EvaluationResult(event.event_id, Decision.dropped)

        matched_ids = [r.trigger_id for r in matched]
        deferred_ids: list[str] = []
        for r in (r for r in matched if r.type == TriggerType.deferred):
            self.deferred_sink.enqueue(DeferredItem(
                trigger_id=r.trigger_id, customer_id=event.customer_id, event_id=event.event_id,
                wait_period=r.deferred.wait_period if r.deferred else "PT0S",
                expiry=r.deferred.expiry if r.deferred else "P3D",
                occurred_at=event.occurred_at,
            ))
            deferred_ids.append(r.trigger_id)

        in_session = [r for r in matched if r.type == TriggerType.in_session]
        fire: FireMessage | None = None
        suppressed = False
        if in_session:
            candidates = [
                MatchCandidate(trigger=r, signal_at=event.occurred_at) for r in in_session
            ]
            decision = self.engine.reserve(event.customer_id, candidates, now)
            if decision.approved:
                winner = next(r for r in in_session if r.trigger_id == decision.winner_trigger_id)
                fire = FireMessage(
                    reservation_id=decision.reservation_id,
                    customer_id=event.customer_id,
                    trigger_id=decision.winner_trigger_id,
                    event_id=event.event_id,
                    message_template_ref=winner.message_template_ref,
                    occurred_at=event.occurred_at,
                )
                self.fire_sink.emit(fire)
            else:
                suppressed = True
                self.suppression_sink.record(decision)   # Z1 -> M14

        if fire is not None:
            status = Decision.fired
        elif suppressed:
            status = Decision.suppressed
        elif deferred_ids:
            status = Decision.deferred
        else:
            status = Decision.dropped
        return EvaluationResult(event.event_id, status, matched_ids, deferred_ids, fire, suppressed)
