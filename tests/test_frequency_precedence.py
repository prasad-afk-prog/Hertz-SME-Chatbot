"""A6 (POA/05) — Frequency Cap & Precedence Engine: sliding-window caps, global
cap, cooldown, deterministic precedence, reserve->confirm/rollback, and the
concurrency invariant (never more than the cap).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from generator.models import (
    FrequencyCap,
    MatchCandidate,
    SignalType,
    SuppressionReason,
    TriggerConfig,
    TriggerMatch,
)
from services.event_pipeline.frequency import (
    EngagementLedger,
    FrequencyPrecedenceEngine,
    PerCustomerLock,
    bootstrap,
)

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
CUST = "hfb-cust-1"


def _engine_db():
    e = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    bootstrap.create_all(e)
    return e


def trig(tid, per="P7D", cap_max=1, precedence=100, conditions=None,
         signal=SignalType.search_no_convert) -> TriggerConfig:
    return TriggerConfig(
        trigger_id=tid,
        match=TriggerMatch(signal_type=signal, conditions=conditions or []),
        frequency_cap=FrequencyCap(per=per, max=cap_max),
        precedence=precedence,
    )


def cand(tid, at=T0, **kw) -> MatchCandidate:
    return MatchCandidate(trigger=trig(tid, **kw), signal_at=at)


@pytest.fixture
def engine():
    return FrequencyPrecedenceEngine(EngagementLedger(_engine_db()))


# --- frequency cap ------------------------------------------------------ #
def test_first_match_is_approved(engine):
    d = engine.reserve(CUST, [cand("t1")], T0)
    assert d.approved and d.winner_trigger_id == "t1" and d.reservation_id


def test_second_within_window_is_capped(engine):
    engine.reserve(CUST, [cand("t1")], T0)
    d = engine.reserve(CUST, [cand("t1")], T0 + timedelta(days=1))
    assert not d.approved
    assert d.suppression_reason == SuppressionReason.frequency_cap


def test_sliding_window_expiry_reopens_the_cap(engine):
    engine.reserve(CUST, [cand("t1")], T0)
    d = engine.reserve(CUST, [cand("t1")], T0 + timedelta(days=8))   # outside P7D
    assert d.approved


# --- reserve -> confirm / rollback ------------------------------------- #
def test_rollback_frees_the_slot(engine):
    first = engine.reserve(CUST, [cand("t1")], T0)
    engine.rollback(first.reservation_id)
    again = engine.reserve(CUST, [cand("t1")], T0 + timedelta(hours=1))
    assert again.approved            # a failed delivery must not burn the cap


def test_confirm_keeps_the_slot_consumed(engine):
    first = engine.reserve(CUST, [cand("t1")], T0)
    engine.confirm(first.reservation_id)
    again = engine.reserve(CUST, [cand("t1")], T0 + timedelta(hours=1))
    assert not again.approved


# --- precedence -------------------------------------------------------- #
def test_highest_precedence_wins_and_loser_logged(engine):
    d = engine.reserve(CUST, [cand("low", precedence=100), cand("high", precedence=200)], T0)
    assert d.winner_trigger_id == "high"
    assert d.losers["low"] == SuppressionReason.precedence_loss


def test_tiebreak_specificity_then_recency_then_id():
    eng = FrequencyPrecedenceEngine(EngagementLedger(_engine_db()))
    # equal precedence; more match conditions is more specific -> wins
    d = eng.reserve(CUST, [
        cand("generic", precedence=100, conditions=[]),
        cand("specific", precedence=100, conditions=[{"step": "payment"}]),
    ], T0)
    assert d.winner_trigger_id == "specific"

    eng2 = FrequencyPrecedenceEngine(EngagementLedger(_engine_db()))
    # equal precedence + specificity; the more recent signal wins
    d2 = eng2.reserve(CUST, [
        cand("older", at=T0 - timedelta(minutes=5)),
        cand("newer", at=T0),
    ], T0)
    assert d2.winner_trigger_id == "newer"


def test_precedence_is_order_independent():
    a = [cand("x", precedence=100), cand("y", precedence=200)]
    d1 = FrequencyPrecedenceEngine(EngagementLedger(_engine_db())).reserve(CUST, a, T0)
    d2 = FrequencyPrecedenceEngine(EngagementLedger(_engine_db())).reserve(CUST, list(reversed(a)), T0)
    assert d1.winner_trigger_id == d2.winner_trigger_id == "y"


# --- global cap + cooldown --------------------------------------------- #
def test_global_cap_suppresses_across_triggers():
    eng = FrequencyPrecedenceEngine(
        EngagementLedger(_engine_db()), global_cap=FrequencyCap(per="P1D", max=1)
    )
    assert eng.reserve(CUST, [cand("a")], T0).approved
    d = eng.reserve(CUST, [cand("b")], T0 + timedelta(hours=1))   # different trigger
    assert not d.approved
    assert d.suppression_reason == SuppressionReason.global_cap


def test_cooldown_blocks_inside_quiet_period():
    eng = FrequencyPrecedenceEngine(EngagementLedger(_engine_db()), cooldown="PT1H")
    assert eng.reserve(CUST, [cand("a")], T0).approved
    blocked = eng.reserve(CUST, [cand("b")], T0 + timedelta(minutes=30))
    assert not blocked.approved and blocked.suppression_reason == SuppressionReason.cooldown
    assert eng.reserve(CUST, [cand("b")], T0 + timedelta(hours=2)).approved


# --- concurrency invariant (POA/05 §6) --------------------------------- #
def test_concurrent_reserves_never_exceed_cap():
    import threading

    eng = FrequencyPrecedenceEngine(EngagementLedger(_engine_db()), lock=PerCustomerLock())
    results: list[bool] = []
    lock = threading.Lock()

    def worker():
        d = eng.reserve(CUST, [cand("t1")], T0)
        with lock:
            results.append(d.approved)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 1, f"cap breached: {sum(results)} engagements fired"
