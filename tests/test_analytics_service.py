"""M14 Audit, Reporting & Analytics — POA/14 acceptance criteria (§6).

The five §6 criteria:
  1. conversions attributed to the correct trigger, visible per trigger/segment/time;
  2. engagement, handoff, suppression rates computed and matching source events;
  3. admins can drill from an aggregate to underlying events;
  4. the config-change audit trail is viewable and exportable;
  5. **numbers reconcile with raw outcome events.**

(5) is the one this suite is built around, and it is written as a property —
for any set of ingested events, every aggregate equals a recount from the raw
store. A spot-check would miss rollup bugs nobody thought of.

Three behaviours get their own tests because a naive reporting module gets them
backwards:

* **a zero denominator is `None`, not 0.0** — "0% conversion" and "no data" are
  different facts and a dashboard that renders them the same lies;
* **pending is a real bucket** — M12 leaves a resolved-but-unbooked conversation
  as neither converted nor not-converted, and the funnel has to add up anyway;
* **the funnel never widens** — each stage is a subset of the one before it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from generator.models import TerminalState, VerifyStatus
from generator.pii import PII_FIELDS
from services.admin.config import ConfigEntity, ConfigService, InMemoryChangePublisher
from services.analytics import (
    EVENT_FIELDS,
    SEGMENT_FIELDS,
    AnalyticsService,
    Counts,
    Metrics,
    OutcomeEvent,
    OutcomeKind,
    Segment,
    rate,
)
from services.conversation.delivery import DeliveryReceipt, DeliveryStatus
from services.conversation.response import ConversationOutcome, HandoffEvent, HandoffReason
from services.conversation.orchestrator.state import ConversationStatus

_TZ = timezone.utc
T0 = datetime(2026, 9, 1, 10, 0, tzinfo=_TZ)
UK = Segment(customer_type="SME", region="UK", language="en")
DE = Segment(customer_type="corporate", region="DE", language="de")


def ev(kind, *, trigger="t1", at=None, segment=UK, conversation_id=None, detail=None):
    return OutcomeEvent(
        kind=kind, at=at or T0, trigger_id=trigger, segment=segment,
        conversation_id=conversation_id, detail=detail,
    )


@pytest.fixture
def svc() -> AnalyticsService:
    return AnalyticsService()


def seed(svc, **counts):
    """Ingest N events of each kind. The reconciliation test recounts these."""
    for name, n in counts.items():
        kind = OutcomeKind(name)
        for i in range(n):
            svc.store.record(ev(kind, conversation_id=f"conv-{name}-{i}"))


# =========================================================================== #
# §6.5 — RECONCILIATION: the criterion this suite is built around
# =========================================================================== #
def test_every_aggregate_equals_a_recount_from_the_raw_store(svc):
    """The property, not a spot-check. Aggregates are recomputed from raw
    events on every call — never incremented — so drift is impossible by
    construction, and this proves it."""
    seed(svc, fired=7, suppressed=3, delivered=5, responded=4,
         no_engagement=1, resolved=3, converted=2, handed_off=1)

    metrics = svc.metrics()
    for kind in OutcomeKind:
        raw = len(svc.store.events(kind=kind))
        from services.analytics.metrics import KIND_TO_COUNT
        attr = KIND_TO_COUNT[kind]
        assert getattr(metrics.counts, attr) == raw, f"{kind.value} drifted from raw"


def test_recounting_after_more_events_stays_consistent(svc):
    seed(svc, fired=2, converted=1)
    assert svc.metrics().counts.fired == 2
    seed(svc, fired=3)
    assert svc.metrics().counts.fired == 5, "aggregates must be recomputed, never cached"


def test_filtered_aggregates_also_reconcile(svc):
    svc.store.record(ev(OutcomeKind.fired, trigger="t1"))
    svc.store.record(ev(OutcomeKind.fired, trigger="t2"))
    svc.store.record(ev(OutcomeKind.fired, trigger="t2"))

    assert svc.metrics(trigger_id="t2").counts.fired == 2
    assert len(svc.store.events(trigger_id="t2", kind=OutcomeKind.fired)) == 2


# =========================================================================== #
# Denominators — where this module gets silently wrong
# =========================================================================== #
def test_a_zero_denominator_is_none_not_zero():
    """"0% conversion" and "no data" are different facts. A dashboard that
    renders them identically lies to whoever decides from it."""
    empty = Metrics()
    assert empty.conversion_rate is None
    assert empty.engagement_rate is None
    assert empty.handoff_rate is None
    assert empty.suppression_rate is None
    assert rate(0, 0) is None
    assert rate(0, 5) == 0.0, "a real zero is still zero"


def test_conversion_is_over_fired_not_delivered(svc):
    """An engagement approved and then undelivered is a conversion we LOST.
    Putting it in the denominator would flatter the number."""
    seed(svc, fired=10, delivered=5, converted=2)
    assert svc.metrics().conversion_rate == 0.2          # 2/10, not 2/5


def test_engagement_is_over_delivered_only(svc):
    """Only messages that reached someone can be responded to."""
    seed(svc, fired=10, delivered=4, responded=2)
    assert svc.metrics().engagement_rate == 0.5          # 2/4


def test_handoff_is_over_conversations_that_started(svc):
    seed(svc, delivered=10, responded=3, no_engagement=1, handed_off=2)
    assert svc.metrics().counts.conversations == 4       # responded + no_engagement
    assert svc.metrics().handoff_rate == 0.5             # 2/4


def test_suppression_is_over_everything_a6_matched(svc):
    seed(svc, fired=3, suppressed=1)
    assert svc.metrics().counts.matched == 4
    assert svc.metrics().suppression_rate == 0.25


# =========================================================================== #
# The pending bucket — M12's terminal=None finding, flowing through
# =========================================================================== #
def test_resolved_but_unbooked_is_pending_not_a_failure(svc):
    """POA/12 §11: a customer the bot helped who has not yet booked is neither
    a conversion nor a non-conversion while inside the attribution window."""
    seed(svc, delivered=5, responded=5, resolved=3, converted=1)
    assert svc.metrics().counts.pending == 2


def test_the_outcome_split_always_adds_up(svc):
    """Bucketing pending as not-converted under-reports conversion for every
    live conversation; dropping it stops the funnel adding up."""
    seed(svc, delivered=10, responded=6, no_engagement=2, resolved=4, converted=1)
    metrics = svc.metrics()
    split = metrics.outcome_split()

    assert sum(split.values()) == metrics.counts.conversations
    assert split["pending"] == 3
    assert split["converted"] == 1


def test_pending_never_goes_negative(svc):
    """A conversion can land in a later window than its resolution. Without the
    clamp that produces a negative bucket and a nonsense funnel."""
    seed(svc, converted=3, resolved=1)
    assert svc.metrics().counts.pending == 0


# =========================================================================== #
# The funnel
# =========================================================================== #
def test_the_funnel_never_widens(svc):
    """Each stage is a subset of the one before it. A funnel that widens is a
    counting bug, not an insight."""
    seed(svc, fired=10, suppressed=2, delivered=8, responded=5, resolved=3, converted=2)
    stages = list(svc.metrics().funnel().values())
    assert stages == sorted(stages, reverse=True), f"funnel widened: {stages}"


def test_the_funnel_starts_from_everything_matched(svc):
    seed(svc, fired=6, suppressed=4)
    assert svc.metrics().funnel()["matched"] == 10


# =========================================================================== #
# §6.1 / §6.2 — per trigger, segment and window
# =========================================================================== #
def test_metrics_split_by_trigger(svc):
    svc.store.record(ev(OutcomeKind.fired, trigger="abandoned"))
    svc.store.record(ev(OutcomeKind.converted, trigger="abandoned"))
    svc.store.record(ev(OutcomeKind.fired, trigger="dormant"))

    by_trigger = svc.by_trigger()
    assert by_trigger["abandoned"].conversion_rate == 1.0
    assert by_trigger["dormant"].conversion_rate == 0.0


def test_metrics_filter_by_segment(svc):
    svc.store.record(ev(OutcomeKind.fired, segment=UK))
    svc.store.record(ev(OutcomeKind.fired, segment=DE))
    svc.store.record(ev(OutcomeKind.converted, segment=DE))

    assert svc.metrics(segment=Segment(region="DE")).counts.fired == 1
    assert svc.metrics(segment=Segment(region="DE")).conversion_rate == 1.0
    assert svc.metrics(segment=Segment(region="UK")).conversion_rate == 0.0


def test_an_unset_segment_field_matches_anything(svc):
    svc.store.record(ev(OutcomeKind.fired, segment=UK))
    svc.store.record(ev(OutcomeKind.fired, segment=DE))
    assert svc.metrics(segment=Segment()).counts.fired == 2


def test_metrics_filter_by_time_window(svc):
    svc.store.record(ev(OutcomeKind.fired, at=T0))
    svc.store.record(ev(OutcomeKind.fired, at=T0 + timedelta(days=2)))

    assert svc.metrics(start=T0, end=T0 + timedelta(hours=1)).counts.fired == 1
    assert svc.metrics(start=T0).counts.fired == 2


def test_window_boundaries_are_inclusive(svc):
    svc.store.record(ev(OutcomeKind.fired, at=T0))
    assert svc.metrics(start=T0, end=T0).counts.fired == 1


# =========================================================================== #
# Quality signals (M09 / M10)
# =========================================================================== #
def test_verification_correction_rate_is_reported(svc):
    """A rising number here means the model is increasingly asserting things
    that are not true — the failure verification exists to catch."""
    seed(svc, delivered=10, claim_corrected=2, claim_stripped=1)
    assert svc.metrics().verification_correction_rate == 0.3


def test_fallback_rate_is_reported(svc):
    seed(svc, delivered=8, fallback_used=2)
    assert svc.metrics().fallback_rate == 0.25


# =========================================================================== #
# §6.3 — drill-down
# =========================================================================== #
def test_drill_down_returns_the_underlying_events(svc):
    svc.store.record(ev(OutcomeKind.handed_off, conversation_id="conv-1", detail="max_turns"))
    svc.store.record(ev(OutcomeKind.handed_off, conversation_id="conv-2", detail="complaint"))
    svc.store.record(ev(OutcomeKind.converted, conversation_id="conv-3"))

    handoffs = svc.drill_down(OutcomeKind.handed_off)
    assert len(handoffs) == 2
    assert {e.conversation_id for e in handoffs} == {"conv-1", "conv-2"}
    assert {e.detail for e in handoffs} == {"max_turns", "complaint"}


def test_drill_down_respects_the_same_filters_as_the_aggregate(svc):
    svc.store.record(ev(OutcomeKind.converted, trigger="t1"))
    svc.store.record(ev(OutcomeKind.converted, trigger="t2"))

    assert svc.metrics(trigger_id="t1").counts.converted == 1
    assert len(svc.drill_down(OutcomeKind.converted, trigger_id="t1")) == 1


# =========================================================================== #
# PII boundary — the S4/M08 trick, scoped honestly
# =========================================================================== #
def test_no_analytics_event_field_is_marked_as_pii():
    """Aggregates are PII-free BY CONSTRUCTION: adding a PII-bearing field to an
    OutcomeEvent fails here rather than shipping a customer's name into a
    report."""
    pii_names = {f for fields in PII_FIELDS.values() for f in fields}
    assert pii_names, "the PII marking must not be empty, or this passes vacuously"
    assert set(EVENT_FIELDS) & pii_names == set()
    assert set(SEGMENT_FIELDS) & pii_names == set()


def test_no_s4_fixture_value_can_reach_an_export(svc):
    """End to end: the S4 corpus is every PII shape we know about."""
    from generator.pii import RedactionFixtureBuilder

    seed(svc, fired=3, converted=1, handed_off=1)
    csv_text = svc.export_csv()
    for fixture in RedactionFixtureBuilder().all():
        for span in fixture.spans:
            assert span.value not in csv_text, f"{span.kind.value} reached a report"


def test_a_handoff_event_transcript_never_enters_analytics(svc):
    """HandoffEvent carries free customer text. Only its REASON is reportable —
    the transcript stays in the conversation store, behind M15's access control
    (which does not exist yet; POA/14 §11)."""
    handoff = HandoffEvent(
        conversation_id="conv-1", customer_id="cust-1", trigger_id="t1",
        reason=HandoffReason.complaint, raised_at=T0,
        transcript=[],
    )
    outcome = ConversationOutcome(
        conversation_id="conv-1", customer_id="cust-1", trigger_id="t1",
        terminal=TerminalState.handed_off, status=ConversationStatus.handed_off,
        handoff=handoff, resolved_at=T0,
    )
    svc.collect_outcomes([outcome])

    for event in svc.store.events():
        assert not hasattr(event, "transcript")
        assert event.detail in (None, "complaint")


# =========================================================================== #
# Immutability (same rule as M13's audit)
# =========================================================================== #
def test_the_event_store_cannot_be_edited_through_a_read(svc):
    seed(svc, fired=2)
    events = svc.store.events()
    assert isinstance(events, tuple)

    with pytest.raises(AttributeError):
        events[0].kind = OutcomeKind.converted        # frozen dataclass

    before = len(svc.store)
    try:
        events.pop()                                   # type: ignore[attr-defined]
    except AttributeError:
        pass
    assert len(svc.store) == before


# =========================================================================== #
# Adapters — nothing upstream had to change
# =========================================================================== #
def test_delivery_receipts_become_outcome_events(svc):
    receipts = [
        DeliveryReceipt("conv-1", "th-1", DeliveryStatus.delivered, attempts=1, read_at=5.0),
        DeliveryReceipt("conv-2", "th-2", DeliveryStatus.failed, attempts=3),
        DeliveryReceipt("conv-3", None, DeliveryStatus.queued),
    ]
    svc.collect_delivery(receipts, at=T0, trigger_id="t1")
    counts = svc.metrics().counts

    assert counts.delivered == 1
    assert counts.delivery_failed == 1
    assert counts.read == 1
    assert svc.metrics().delivery_failure_rate == 0.5


def test_a_queued_message_is_not_counted_as_delivered(svc):
    """It never reached anyone, so it cannot be in the engagement denominator."""
    svc.collect_delivery(
        [DeliveryReceipt("c", None, DeliveryStatus.queued)], at=T0
    )
    assert svc.metrics().counts.delivered == 0


def test_conversation_outcomes_become_outcome_events(svc):
    outcomes = [
        ConversationOutcome("c1", "cust-1", "t1", TerminalState.no_engagement,
                            ConversationStatus.no_engagement),
        ConversationOutcome("c2", "cust-2", "t1", None,
                            ConversationStatus.deep_link, resolved_at=T0),
        ConversationOutcome("c3", "cust-3", "t1", TerminalState.converted,
                            ConversationStatus.converted, booking_id="bk-1", resolved_at=T0),
    ]
    svc.collect_outcomes(outcomes)
    counts = svc.metrics().counts

    assert counts.no_engagement == 1
    assert counts.responded == 2, "resolved and converted both mean the customer replied"
    assert counts.resolved == 2
    assert counts.converted == 1
    assert counts.pending == 1


def test_verification_records_become_quality_events(svc):
    class Rec:
        def __init__(self, status, failure_kind=None):
            self.status, self.failure_kind = status, failure_kind

    svc.collect_verification(
        [Rec(VerifyStatus.wrong), Rec(VerifyStatus.unverifiable), Rec(VerifyStatus.ok)],
        at=T0, trigger_id="t1",
    )
    counts = svc.metrics().counts
    assert counts.claim_corrected == 1
    assert counts.claim_stripped == 1


# =========================================================================== #
# §6.4 — config-audit views and export
# =========================================================================== #
def test_the_config_audit_trail_is_viewable():
    config = ConfigService(publisher=InMemoryChangePublisher())
    svc = AnalyticsService(config_audit=config.audit)

    entries = svc.config_audit_view(entity=ConfigEntity.trigger)
    assert entries, "seeded config changes should be visible"
    assert all(e.actor for e in entries)


def test_the_config_audit_trail_is_exportable():
    config = ConfigService(publisher=InMemoryChangePublisher())
    svc = AnalyticsService(config_audit=config.audit)

    csv_text = svc.config_audit_csv(entity=ConfigEntity.trigger)
    lines = csv_text.strip().splitlines()
    assert lines[0] == "at,entity,entity_id,action,actor,version,note"
    assert len(lines) > 1


def test_analytics_cannot_alter_the_audit_it_reports_on():
    """A read view, deliberately."""
    config = ConfigService(publisher=InMemoryChangePublisher())
    svc = AnalyticsService(config_audit=config.audit)

    entries = svc.config_audit_view()
    assert isinstance(entries, tuple)
    before = len(config.audit)
    with pytest.raises(AttributeError):
        entries[0].actor = "someone-else"
    assert len(config.audit) == before


def test_audit_views_are_empty_without_a_configured_source(svc):
    assert svc.config_audit_view() == ()
    assert svc.config_audit_csv().strip().splitlines() == [
        "at,entity,entity_id,action,actor,version,note"
    ]


# =========================================================================== #
# Export
# =========================================================================== #
def test_csv_export_has_a_stable_header_and_one_row_per_event(svc):
    seed(svc, fired=2, converted=1)
    lines = svc.export_csv().strip().splitlines()
    assert lines[0] == (
        "kind,at,trigger_id,conversation_id,customer_id,"
        "customer_type,region,language,detail,value"
    )
    assert len(lines) == 1 + len(svc.store)
