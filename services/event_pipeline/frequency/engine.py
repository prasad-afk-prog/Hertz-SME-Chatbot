"""FrequencyPrecedenceEngine (A6 / M05) — decide whether to engage and which
single trigger wins (POA/05 §3.2).

reserve() runs under a per-customer lock: cooldown -> global cap -> per-trigger
cap filter -> precedence winner -> record a `reserved` ledger row. M08 then
confirm()s on delivery or rollback()s on failure, so a failed send doesn't burn
the cap. Every suppression carries a machine-readable reason for M14.

Cap math delegates to reference.would_fire (the executable sliding-window spec),
so the service and the golden/invariant suites test the same rule.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from generator.durations import parse_duration
from generator.models import EngagementDecision, FrequencyCap, MatchCandidate, SuppressionReason
from generator.reference import would_fire

from .ledger import EngagementLedger
from .lock import NullLock
from .precedence import choose_winner


class FrequencyPrecedenceEngine:
    def __init__(
        self,
        ledger: EngagementLedger,
        *,
        global_cap: FrequencyCap | None = None,
        cooldown: str | None = None,        # ISO-8601 quiet period after any engagement
        lock=None,
    ) -> None:
        self.ledger = ledger
        self.global_cap = global_cap
        self.cooldown = parse_duration(cooldown) if cooldown else None
        self.lock = lock or NullLock()

    def reserve(
        self, customer_id: str, candidates: list[MatchCandidate], now: datetime
    ) -> EngagementDecision:
        if not candidates:
            return EngagementDecision(approved=False, customer_id=customer_id)

        with self.lock(customer_id):
            # 1. global cross-trigger cooldown
            if self.cooldown is not None:
                last = self.ledger.last_engagement_at(customer_id)
                if last is not None and (now - last) < self.cooldown:
                    return self._suppress_all(customer_id, candidates, SuppressionReason.cooldown)

            # 2. per-customer global cap
            if self.global_cap is not None and not would_fire(
                self.ledger.fire_times(customer_id), now, self.global_cap
            ):
                return self._suppress_all(customer_id, candidates, SuppressionReason.global_cap)

            # 3. per-trigger cap filter
            eligible: list[MatchCandidate] = []
            losers: dict[str, SuppressionReason] = {}
            for c in candidates:
                times = self.ledger.fire_times(customer_id, c.trigger.trigger_id)
                if would_fire(times, now, c.trigger.frequency_cap):
                    eligible.append(c)
                else:
                    losers[c.trigger.trigger_id] = SuppressionReason.frequency_cap
            if not eligible:
                return EngagementDecision(
                    approved=False, customer_id=customer_id,
                    suppression_reason=SuppressionReason.frequency_cap, losers=losers,
                )

            # 4. precedence winner; losers logged for M14
            winner = choose_winner(eligible)
            for c in eligible:
                if c is not winner:
                    losers[c.trigger.trigger_id] = SuppressionReason.precedence_loss

            # 5. reserve the slot (finalised by M08 confirm/rollback)
            reservation_id = uuid.uuid4().hex
            self.ledger.reserve(reservation_id, customer_id, winner.trigger.trigger_id, now)
            return EngagementDecision(
                approved=True, customer_id=customer_id,
                reservation_id=reservation_id, winner_trigger_id=winner.trigger.trigger_id,
                losers=losers,
            )

    def confirm(self, reservation_id: str) -> None:
        """M08 delivered — finalise the slot (POA/05 §3.2)."""
        self.ledger.set_status(reservation_id, "confirmed")

    def rollback(self, reservation_id: str) -> None:
        """M08 failed to deliver — release the slot so the cap isn't burned."""
        self.ledger.set_status(reservation_id, "rolled_back")

    def _suppress_all(
        self, customer_id: str, candidates: list[MatchCandidate], reason: SuppressionReason
    ) -> EngagementDecision:
        return EngagementDecision(
            approved=False, customer_id=customer_id, suppression_reason=reason,
            losers={c.trigger.trigger_id: reason for c in candidates},
        )
