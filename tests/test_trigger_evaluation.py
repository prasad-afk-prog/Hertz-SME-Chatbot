"""A5 (POA/04) — Trigger Evaluation Engine: the sandboxed rule DSL, node-N
routing (in-session fire / deferred enqueue / drop), cap suppression via A6,
precedence over multiple matches, idempotency, and the stream path.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from generator.models import (
    Event,
    EventContext,
    FrequencyCap,
    SignalType,
    TriggerConfig,
    TriggerMatch,
    TriggerType,
)
from services.event_pipeline.frequency import EngagementLedger, FrequencyPrecedenceEngine
from services.event_pipeline.frequency import bootstrap as freq_bootstrap
from services.event_pipeline.triggers import (
    Decision,
    InMemoryDeferredSink,
    InMemoryFireSink,
    InMemorySuppressionSink,
    StaticRuleSource,
    TriggerEvaluator,
    eval_condition,
    matching_rules,
    parse_stream_fields,
)

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def _sqlite():
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )


def a6() -> FrequencyPrecedenceEngine:
    engine = _sqlite()
    freq_bootstrap.create_all(engine)
    return FrequencyPrecedenceEngine(EngagementLedger(engine))


def ev(eid, signal=SignalType.booking_abandoned, cid="cust-1", when=T0, **ctx) -> Event:
    return Event(
        event_id=eid, customer_id=cid, session_id="s1",
        signal_type=signal, occurred_at=when, context=EventContext(**ctx),
    )


def trig(tid, precedence=100, signal=SignalType.booking_abandoned,
         type_=TriggerType.in_session, conditions=None) -> TriggerConfig:
    return TriggerConfig(
        trigger_id=tid, match=TriggerMatch(signal_type=signal, conditions=conditions or []),
        type=type_, frequency_cap=FrequencyCap(per="P7D", max=1), precedence=precedence,
    )


def make_evaluator(engine=None, rules=None):
    fs, ds, ss = InMemoryFireSink(), InMemoryDeferredSink(), InMemorySuppressionSink()
    te = TriggerEvaluator(
        engine or a6(),
        rule_source=StaticRuleSource(rules) if rules is not None else None,
        fire_sink=fs, deferred_sink=ds, suppression_sink=ss,
    )
    return te, fs, ds, ss


# --- DSL ---------------------------------------------------------------- #
def test_dsl_operators():
    e = ev("e1", step="payment", dwell_ms=95000, pickup="LHR")
    assert eval_condition(e, {"field": "context.step", "op": "in", "value": ["payment", "review"]})
    assert eval_condition(e, {"field": "context.dwell_ms", "op": "gt", "value": 60000})
    assert eval_condition(e, {"field": "context.pickup", "op": "exists"})
    assert eval_condition(e, {"field": "signal_type", "op": "eq", "value": "booking_abandoned"})
    assert not eval_condition(e, {"field": "context.step", "op": "eq", "value": "confirm"})
    assert not eval_condition(e, {"field": "context.error_code", "op": "exists"})


def test_dsl_rejects_unknown_op():
    with pytest.raises(ValueError):
        eval_condition(ev("e1"), {"field": "signal_type", "op": "matches", "value": "x"})


def test_matching_requires_signal_and_all_conditions():
    rule = trig("t", conditions=[{"field": "context.step", "op": "in", "value": ["payment"]}])
    assert matching_rules(ev("e1", step="payment"), [rule]) == [rule]
    assert matching_rules(ev("e2", step="review"), [rule]) == []            # condition fails
    assert matching_rules(ev("e3", signal=SignalType.error_hit, step="payment"), [rule]) == []  # signal


# --- node-N routing ----------------------------------------------------- #
def test_in_session_match_fires():
    te, fs, ds, ss = make_evaluator()
    r = te.evaluate(ev("e1", SignalType.booking_abandoned))
    assert r.status == Decision.fired
    assert len(fs.messages) == 1
    msg = fs.messages[0]
    assert msg.trigger_id == "booking_abandoned_v1" and msg.reservation_id
    assert msg.message_template_ref == "tmpl_booking_abandoned"


def test_deferred_match_enqueues_and_does_not_fire():
    te, fs, ds, ss = make_evaluator()
    r = te.evaluate(ev("e1", SignalType.repeated_search))
    assert r.status == Decision.deferred
    assert [i.trigger_id for i in ds.items] == ["repeated_search_v1"]
    assert fs.messages == []


def test_no_matching_rule_is_dropped():
    te, fs, ds, ss = make_evaluator(rules=[trig("only_ba")])
    r = te.evaluate(ev("e1", SignalType.error_hit))
    assert r.status == Decision.dropped
    assert not fs.messages and not ds.items


# --- cap + precedence via A6 ------------------------------------------- #
def test_second_event_suppressed_by_cap():
    engine = a6()
    te, fs, _, ss = make_evaluator(engine=engine)
    assert te.evaluate(ev("e1")).status == Decision.fired
    r2 = te.evaluate(ev("e2"))                       # same customer+trigger, within window
    assert r2.status == Decision.suppressed
    assert len(fs.messages) == 1 and len(ss.suppressions) == 1


def test_precedence_winner_fires_over_multiple_matches():
    rules = [trig("low", precedence=100), trig("high", precedence=200)]
    te, fs, _, _ = make_evaluator(rules=rules)
    r = te.evaluate(ev("e1", SignalType.booking_abandoned))
    assert set(r.matched) == {"low", "high"}
    assert fs.messages[0].trigger_id == "high"       # highest precedence wins


# --- idempotency -------------------------------------------------------- #
def test_redelivered_event_is_deduped():
    te, fs, _, _ = make_evaluator()
    assert te.evaluate(ev("e1")).status == Decision.fired
    assert te.evaluate(ev("e1")).status == Decision.duplicate
    assert len(fs.messages) == 1                     # not fired twice


def test_rollback_lets_a_later_event_fire_again():
    engine = a6()
    te, fs, _, _ = make_evaluator(engine=engine)
    r1 = te.evaluate(ev("e1", when=T0))
    engine.rollback(r1.fire.reservation_id)          # M08 delivery failed
    r2 = te.evaluate(ev("e2", when=T0 + timedelta(hours=1)))
    assert r1.status == r2.status == Decision.fired  # slot was freed
    assert len(fs.messages) == 2


# --- stream path -------------------------------------------------------- #
def test_parse_stream_fields_roundtrip():
    fields = {
        "event_id": "e1", "customer_id": "cust-1", "session_id": "s1",
        "signal_type": "booking_abandoned", "occurred_at": "2026-09-01T10:00:00+00:00",
        "context": '{"pickup": "LHR", "step": "payment"}',
    }
    e = parse_stream_fields(fields)
    assert e.event_id == "e1" and e.signal_type == SignalType.booking_abandoned
    assert e.context.pickup == "LHR" and e.context.step.value == "payment"


def test_end_to_end_store_relay_evaluate_fires():
    from services.event_pipeline.store import InMemoryStreamPublisher, OutboxRelay, SqlEventStore
    from services.event_pipeline.store import bootstrap as store_bootstrap

    db = _sqlite()
    store_bootstrap.create_all(db)
    store = SqlEventStore(db)
    store.write_event(ev("z1", SignalType.booking_abandoned))

    pub = InMemoryStreamPublisher()
    OutboxRelay(store, pub).run_once()
    _eid, fields = pub.messages[0]

    te, fs, _, _ = make_evaluator()
    r = te.evaluate(parse_stream_fields(fields))
    assert r.status == Decision.fired
    assert fs.messages[0].event_id == "z1"           # ingested event drove a fire
