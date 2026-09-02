"""Login re-evaluation (node S, POA/06 §3.3).

On a customer's login: fetch eligible pending entries, arbitrate them through A6
(cap + precedence — multiple pending entries compete on precedence, POA/06 §10.3),
fire the winner via the FireSink, mark it `raised`, and leave the losers pending
for a later login. The whole hook runs under a per-customer lock so concurrent
logins never double-raise the same entry (POA/06 §6).

A6's reserve() also takes a per-customer lock by default (its own instance,
distinct from this scheduler's), so nesting the two is safe — different lock
objects, no shared-lock deadlock. This scheduler's lock serialises the whole
claim -> arbitrate -> raise sequence.
"""
from __future__ import annotations

from datetime import datetime

from generator.models import FireMessage, MatchCandidate

from ..frequency import FrequencyPrecedenceEngine, NullLock
from ..triggers.sinks import FireSink, RuleSource
from .queue import PendingQueue


class PendingScheduler:
    def __init__(
        self,
        queue: PendingQueue,
        engine: FrequencyPrecedenceEngine,
        rule_source: RuleSource,
        fire_sink: FireSink,
        *,
        lock=None,
    ) -> None:
        self.queue = queue
        self.engine = engine
        self.rule_source = rule_source
        self.fire_sink = fire_sink
        self.lock = lock or NullLock()

    def on_login(self, customer_id: str, now: datetime) -> FireMessage | None:
        with self.lock(customer_id):
            eligible = self.queue.eligible_pending(customer_id, now)
            if not eligible:
                return None

            rules = {r.trigger_id: r for r in self.rule_source.active()}
            candidates: list[MatchCandidate] = []
            entry_by_trigger = {}
            for e in eligible:
                rule = rules.get(e.trigger_id)
                if rule is None:                      # trigger removed from config -> discard
                    self.queue.set_status(e.id, "expired")
                    continue
                candidates.append(MatchCandidate(trigger=rule, signal_at=e.created_at))
                entry_by_trigger[e.trigger_id] = e

            if not candidates:
                return None

            decision = self.engine.reserve(customer_id, candidates, now)
            if not decision.approved:
                return None                           # capped/suppressed -> leave pending

            entry = entry_by_trigger[decision.winner_trigger_id]
            self.queue.set_status(entry.id, "raised")
            winner_rule = rules[decision.winner_trigger_id]
            message = FireMessage(
                reservation_id=decision.reservation_id,
                customer_id=customer_id,
                trigger_id=decision.winner_trigger_id,
                event_id=entry.event_id,
                message_template_ref=winner_rule.message_template_ref,
                occurred_at=now,
            )
            self.fire_sink.emit(message)
            return message
