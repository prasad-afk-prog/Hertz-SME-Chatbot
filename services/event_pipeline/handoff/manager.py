"""HandoffManager (A8 / M07) — route a handoff, package its context, dispatch it
into the agent queue with retry, fall back when there's no agent or the primary
fails, and never drop it silently (POA/07).

Reads RoutingRule's bare match/route/sla dicts directly (kept thin per POA/18 §8
A8 caveat). Records every handoff to the ledger for M14 + audit.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from generator.models import HandoffRequest, RoutingRule

from .adapter import DeadLetterSink, InMemoryDeadLetterSink, QueueAdapter
from .ledger import HandoffLedger
from .packager import package
from .routing import context_of, route_for


class HandoffStatus(str, Enum):
    routed = "routed"                # dispatched to the primary queue
    fallback = "fallback"            # no agent / primary failed -> fallback queue
    dead_lettered = "dead_lettered"  # every queue failed after retries


@dataclass
class HandoffResult:
    status: HandoffStatus
    queue: str
    ticket_ref: str | None
    rule_id: str


class HandoffManager:
    def __init__(
        self,
        adapter: QueueAdapter,
        rules: list[RoutingRule],
        *,
        ledger: HandoffLedger | None = None,
        dead_letter: DeadLetterSink | None = None,
        max_attempts: int = 3,
        agent_available: Callable[[str], bool] | None = None,
    ) -> None:
        self.adapter = adapter
        self.rules = rules
        self.ledger = ledger
        self.dead_letter = dead_letter or InMemoryDeadLetterSink()
        self.max_attempts = max_attempts
        self.agent_available = agent_available or (lambda queue: True)

    def handle(self, request: HandoffRequest) -> HandoffResult:
        rule = route_for(
            context_of(request.language, request.customer_type, request.priority), self.rules
        )
        route = rule.route if rule else {}
        primary = route.get("queue") or "general"
        fallback = (rule.fallback_queue if rule else None) or "general"
        priority = route.get("priority") or request.priority or "normal"
        skill = route.get("skill")
        sla = rule.sla if rule else None
        rule_id = rule.rule_id if rule else "default"

        # after-hours / no-agent: skip the primary queue, go straight to fallback
        attempts: list[tuple[str, HandoffStatus]] = []
        if self.agent_available(primary):
            attempts.append((primary, HandoffStatus.routed))
        if not attempts or fallback != primary:
            attempts.append((fallback, HandoffStatus.fallback))

        for queue, status in attempts:
            payload = package(request, queue=queue, skill=skill, priority=priority, sla=sla)
            ticket_ref = self._dispatch(payload)
            if ticket_ref is not None:
                self._record(request, queue, ticket_ref, rule_id, status)
                return HandoffResult(status, queue, ticket_ref, rule_id)

        # every queue failed after retries — dead-letter, never drop (POA/07 §8)
        payload = package(request, queue=primary, skill=skill, priority=priority, sla=sla)
        self.dead_letter.record(payload, "all queues failed after retries")
        self._record(request, primary, None, rule_id, HandoffStatus.dead_lettered)
        return HandoffResult(HandoffStatus.dead_lettered, primary, None, rule_id)

    def _dispatch(self, payload: dict) -> str | None:
        for _ in range(self.max_attempts):
            try:
                return self.adapter.enqueue(payload)
            except Exception:
                continue
        return None

    def _record(
        self, request: HandoffRequest, queue: str, ticket_ref: str | None,
        rule_id: str, status: HandoffStatus,
    ) -> None:
        if self.ledger is None:
            return
        self.ledger.record(
            id=uuid.uuid4().hex, conversation_id=request.conversation_id,
            customer_id=request.customer_id, queue=queue, ticket_ref=ticket_ref,
            rule_id=rule_id, reason=request.reason.value, status=status.value,
            at=datetime.now(UTC),
        )
