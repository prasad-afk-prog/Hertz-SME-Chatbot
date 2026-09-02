"""Analytics service + reporting queries (M14 tasks 2, 5, 7) — POA/14 §3.

Immutable event store, rollups, drill-down, CSV export, and read views over
M13's config audit.

**Every aggregate is recomputed from the raw events, never incremented.** §6
requires numbers to reconcile with raw outcome events, and the cheapest way to
guarantee that is to have exactly one code path: `metrics()` recounts. A cached
counter that drifts from its source is the classic reporting bug, and it drifts
silently. If this ever needs to be fast, the fix is a materialised view over the
same events (§3.1) — not a counter maintained by hand.

`collect_from` adapts the shapes the other modules already produce (M11
receipts, M12 outcomes, M13 audit) into `OutcomeEvent`s. Nothing upstream had to
change to support reporting, which is the point of task 1's contracts.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Iterable, Sequence

from services.admin.config import AuditEntry, ConfigEntity
from services.admin.config.service import AuditLog

from .events import EVENT_FIELDS, OutcomeEvent, OutcomeKind, Segment, as_row
from .metrics import KIND_TO_COUNT, Counts, Metrics


class AnalyticsStore:
    """Append-only. Reads return tuples — an 'immutable' log a caller can edit
    is not immutable, the same rule M13's audit follows."""

    def __init__(self) -> None:
        self._events: list[OutcomeEvent] = []

    def record(self, event: OutcomeEvent) -> None:
        self._events.append(event)

    def record_many(self, events: Iterable[OutcomeEvent]) -> None:
        for event in events:
            self.record(event)

    def events(
        self,
        *,
        trigger_id: str | None = None,
        kind: OutcomeKind | None = None,
        segment: Segment | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[OutcomeEvent, ...]:
        found = [
            e for e in self._events
            if (trigger_id is None or e.trigger_id == trigger_id)
            and (kind is None or e.kind is kind)
            and (segment is None or segment.matches(e.segment))
            and e.in_window(start, end)
        ]
        return tuple(found)

    def __len__(self) -> int:
        return len(self._events)


class AnalyticsService:
    def __init__(self, store: AnalyticsStore | None = None, config_audit: AuditLog | None = None) -> None:
        self.store = store or AnalyticsStore()
        self.config_audit = config_audit

    # ---- task 3/4: rollups ---------------------------------------------- #
    def metrics(
        self,
        *,
        trigger_id: str | None = None,
        segment: Segment | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Metrics:
        """Recount from raw events. Never incremental — see the module docstring."""
        counts = Counts()
        for event in self.store.events(
            trigger_id=trigger_id, segment=segment, start=start, end=end
        ):
            attr = KIND_TO_COUNT.get(event.kind)
            if attr is not None:
                setattr(counts, attr, getattr(counts, attr) + 1)
        return Metrics(counts=counts)

    def by_trigger(
        self, *, segment: Segment | None = None,
        start: datetime | None = None, end: datetime | None = None,
    ) -> dict[str, Metrics]:
        """Per-trigger performance (§3.3)."""
        triggers = {
            e.trigger_id for e in self.store.events(segment=segment, start=start, end=end)
            if e.trigger_id
        }
        return {
            t: self.metrics(trigger_id=t, segment=segment, start=start, end=end)
            for t in sorted(triggers)
        }

    # ---- task 5: drill-down --------------------------------------------- #
    def drill_down(
        self, kind: OutcomeKind, *, trigger_id: str | None = None,
        segment: Segment | None = None,
        start: datetime | None = None, end: datetime | None = None,
    ) -> tuple[OutcomeEvent, ...]:
        """§6: admins can drill from an aggregate to the underlying events.

        NOTE: this returns analytics events, which carry no PII. Following a
        `conversation_id` from here into the conversation store reaches customer
        text and needs M15's access control — see POA/14 §11.
        """
        return self.store.events(
            kind=kind, trigger_id=trigger_id, segment=segment, start=start, end=end
        )

    def export_csv(self, events: Sequence[OutcomeEvent] | None = None) -> str:
        """§2's CSV export. Aggregate-safe columns only — no transcript."""
        rows = [as_row(e) for e in (events if events is not None else self.store.events())]
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=[
                "kind", "at", "trigger_id", "conversation_id", "customer_id",
                "customer_type", "region", "language", "detail", "value",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue()

    # ---- task 7: config-audit views (from M13) --------------------------- #
    def config_audit_view(
        self, *, entity: ConfigEntity | None = None, entity_id: str | None = None,
    ) -> tuple[AuditEntry, ...]:
        """§3.4 — read view over M13's `config_audit`, for compliance.

        A read view, deliberately: M14 must not be able to alter the audit trail
        it reports on.
        """
        if self.config_audit is None:
            return ()
        return self.config_audit.entries(entity=entity, entity_id=entity_id)

    def config_audit_csv(self, **kw) -> str:
        entries = self.config_audit_view(**kw)
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=["at", "entity", "entity_id", "action", "actor", "version", "note"],
            lineterminator="\n",
        )
        writer.writeheader()
        for entry in entries:
            writer.writerow({
                "at": entry.at,
                "entity": entry.entity.value,
                "entity_id": entry.entity_id,
                "action": entry.action.value,
                "actor": entry.actor,
                "version": entry.version if entry.version is not None else "",
                "note": entry.note or "",
            })
        return buffer.getvalue()

    # ---- task 1: adapters from what the modules already emit ------------- #
    def collect_delivery(self, receipts: Iterable, at: datetime, trigger_id: str | None = None,
                         segment: Segment | None = None) -> int:
        """M11 `DeliveryReceipt`s -> delivered / delivery_failed / read."""
        from services.conversation.delivery import DeliveryStatus

        segment = segment or Segment()
        recorded = 0
        for receipt in receipts:
            if receipt.status is DeliveryStatus.delivered:
                kind = OutcomeKind.delivered
            elif receipt.status is DeliveryStatus.failed:
                kind = OutcomeKind.delivery_failed
            else:
                continue                    # queued/suppressed never reached anyone
            self.store.record(OutcomeEvent(
                kind=kind, at=at, trigger_id=trigger_id,
                conversation_id=receipt.conversation_id, segment=segment,
                value=float(receipt.attempts),
            ))
            recorded += 1
            if receipt.read_at is not None:
                self.store.record(OutcomeEvent(
                    kind=OutcomeKind.read, at=at, trigger_id=trigger_id,
                    conversation_id=receipt.conversation_id, segment=segment,
                ))
                recorded += 1
        return recorded

    def collect_outcomes(self, outcomes: Iterable, segment: Segment | None = None) -> int:
        """M12 `ConversationOutcome`s -> responded / no_engagement / resolved /
        converted / handed_off.

        A resolved-but-unbooked conversation emits `resolved` and NOT
        `converted` — see POA/12 §11 and the pending bucket in metrics.py.
        """
        from generator.models import TerminalState

        segment = segment or Segment()
        recorded = 0
        for outcome in outcomes:
            at = outcome.resolved_at or _epoch()
            common = dict(
                at=at, trigger_id=outcome.trigger_id,
                conversation_id=outcome.conversation_id,
                customer_id=outcome.customer_id, segment=segment,
            )
            if outcome.terminal is TerminalState.no_engagement:
                self.store.record(OutcomeEvent(kind=OutcomeKind.no_engagement, **common))
                recorded += 1
                continue

            # Anything else means the customer replied.
            self.store.record(OutcomeEvent(
                kind=OutcomeKind.responded, **common, value=float(outcome.turns_used)
            ))
            recorded += 1

            if outcome.terminal is TerminalState.handed_off:
                reason = outcome.handoff.reason.value if outcome.handoff else None
                self.store.record(OutcomeEvent(kind=OutcomeKind.handed_off, detail=reason, **common))
                recorded += 1
            elif outcome.resolved_at is not None:
                self.store.record(OutcomeEvent(kind=OutcomeKind.resolved, **common))
                recorded += 1
                if outcome.terminal is TerminalState.converted:
                    self.store.record(OutcomeEvent(kind=OutcomeKind.converted, **common))
                    recorded += 1
        return recorded

    def collect_generation(self, record, at: datetime, trigger_id: str | None = None,
                           segment: Segment | None = None) -> int:
        """M09 `GenerationRecord` -> fallback_used (a quality signal)."""
        if not record.used_fallback:
            return 0
        self.store.record(OutcomeEvent(
            kind=OutcomeKind.fallback_used, at=at, trigger_id=trigger_id,
            segment=segment or Segment(), detail=record.reason.value,
        ))
        return 1

    def collect_verification(self, records: Iterable, at: datetime,
                             trigger_id: str | None = None,
                             segment: Segment | None = None) -> int:
        """M10 `VerificationRecord`s -> claim_corrected / claim_stripped.

        A rising correction rate means the model is increasingly asserting
        things that are not true — the failure the verification layer exists to
        catch, and worth an alert rather than just a chart.
        """
        from generator.models import VerifyStatus

        segment = segment or Segment()
        recorded = 0
        for record in records:
            if record.status is VerifyStatus.wrong:
                kind = OutcomeKind.claim_corrected
            elif record.status is VerifyStatus.unverifiable:
                kind = OutcomeKind.claim_stripped
            else:
                continue
            self.store.record(OutcomeEvent(
                kind=kind, at=at, trigger_id=trigger_id, segment=segment,
                detail=record.failure_kind.value if record.failure_kind else None,
            ))
            recorded += 1
        return recorded


def _epoch() -> datetime:
    from datetime import timezone

    return datetime(1970, 1, 1, tzinfo=timezone.utc)
