"""A7 (POA/06) — Pending-Engagement Queue & Deferred Scheduler: enqueue,
wait/eligibility/expiry (time-travelled), the login re-evaluation hook (arbitrate
via A6 -> fire), the concurrency guard, reconcile, and the A5 -> A7 -> A6 path.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from generator.fixtures import default_triggers
from generator.models import MatchCandidate, SignalType
from services.event_pipeline.frequency import (
    EngagementLedger,
    FrequencyPrecedenceEngine,
    PerCustomerLock,
)
from services.event_pipeline.frequency import bootstrap as freq_bootstrap
from services.event_pipeline.pending import PendingQueue, PendingScheduler
from services.event_pipeline.pending import bootstrap as pending_bootstrap
from services.event_pipeline.triggers import (
    DeferredItem,
    InMemoryFireSink,
    StaticRuleSource,
    TriggerEvaluator,
)

T0 = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
CUST = "cust-1"


def _sqlite():
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )


@pytest.fixture
def queue() -> PendingQueue:
    engine = _sqlite()
    pending_bootstrap.create_all(engine)
    return PendingQueue(engine)


def a6() -> FrequencyPrecedenceEngine:
    engine = _sqlite()
    freq_bootstrap.create_all(engine)
    return FrequencyPrecedenceEngine(EngagementLedger(engine))


def item(tid="repeated_search_v1", cid=CUST, eid="e1", wait="PT0S", expiry="P3D", when=T0) -> DeferredItem:
    return DeferredItem(
        trigger_id=tid, customer_id=cid, event_id=eid,
        wait_period=wait, expiry=expiry, occurred_at=when,
    )


def scheduler(queue, engine=None, lock=None):
    fs = InMemoryFireSink()
    sched = PendingScheduler(
        queue, engine or a6(), StaticRuleSource(default_triggers()), fs, lock=lock
    )
    return sched, fs


# --- enqueue + eligibility --------------------------------------------- #
def test_enqueue_makes_it_pending(queue):
    queue.enqueue(item())
    assert queue.count(CUST, "pending") == 1


def test_wait_period_gates_eligibility(queue):
    queue.enqueue(item(wait="PT1H", when=T0))
    assert queue.eligible_pending(CUST, T0) == []                      # before eligible_at
    assert len(queue.eligible_pending(CUST, T0 + timedelta(hours=2))) == 1


def test_expiry_window_gates_eligibility(queue):
    queue.enqueue(item(expiry="P3D", when=T0))
    assert len(queue.eligible_pending(CUST, T0 + timedelta(days=1))) == 1
    assert queue.eligible_pending(CUST, T0 + timedelta(days=4)) == []   # past expires_at


# --- expiry sweep ------------------------------------------------------- #
def test_expire_due_sweeps_only_overdue_pending(queue):
    queue.enqueue(item(eid="old", expiry="P3D", when=T0))
    queue.enqueue(item(eid="fresh", expiry="P30D", when=T0))
    expired = queue.expire_due(T0 + timedelta(days=4))
    assert len(expired) == 1
    assert queue.count(CUST, "expired") == 1 and queue.count(CUST, "pending") == 1


def test_expire_never_touches_raised(queue):
    eid = queue.enqueue(item())
    queue.set_status(eid, "raised")
    assert queue.expire_due(T0 + timedelta(days=999)) == []
    assert queue.count(CUST, "raised") == 1


# --- login re-evaluation (node S) -------------------------------------- #
def test_login_fires_eligible_pending(queue):
    queue.enqueue(item(tid="repeated_search_v1"))
    sched, fs = scheduler(queue)
    msg = sched.on_login(CUST, T0)
    assert msg is not None and msg.trigger_id == "repeated_search_v1"
    assert msg.reservation_id and msg.event_id == "e1"
    assert queue.count(CUST, "raised") == 1 and queue.count(CUST, "pending") == 0


def test_login_with_nothing_eligible_returns_none(queue):
    sched, fs = scheduler(queue)
    assert sched.on_login(CUST, T0) is None
    assert fs.messages == []


def test_login_precedence_picks_winner_and_keeps_loser_pending(queue):
    queue.enqueue(item(tid="repeated_search_v1", eid="r"))   # precedence 70
    queue.enqueue(item(tid="dormant_v1", eid="d"))           # precedence 50
    sched, fs = scheduler(queue)
    msg = sched.on_login(CUST, T0)
    assert msg.trigger_id == "repeated_search_v1"            # higher precedence wins
    assert queue.count(CUST, "raised") == 1
    assert queue.count(CUST, "pending") == 1                 # dormant retained for next login


def test_login_suppressed_by_cap_leaves_entry_pending():
    engine = a6()
    # burn the repeated_search cap directly
    rule = next(t for t in default_triggers() if t.trigger_id == "repeated_search_v1")
    engine.reserve(CUST, [MatchCandidate(trigger=rule, signal_at=T0)], T0)

    q = PendingQueue(_bootstrapped())
    q.enqueue(item(tid="repeated_search_v1"))
    sched, fs = scheduler(q, engine=engine)
    assert sched.on_login(CUST, T0) is None                  # cap already hit
    assert q.count(CUST, "pending") == 1                     # still there for a later window


def test_concurrent_logins_raise_at_most_once(queue):
    import threading

    queue.enqueue(item(tid="repeated_search_v1"))
    sched, fs = scheduler(queue, lock=PerCustomerLock())
    fired: list[object] = []
    guard = threading.Lock()

    def worker():
        m = sched.on_login(CUST, T0)
        with guard:
            fired.append(m)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for m in fired if m is not None) == 1
    assert queue.count(CUST, "raised") == 1


# --- reconcile stuck ---------------------------------------------------- #
def test_reconcile_releases_stuck_raising(queue):
    eid = queue.enqueue(item())
    queue.set_status(eid, "raising")                         # crashed mid-raise
    released = queue.reconcile_stuck(datetime.now(UTC) + timedelta(hours=1), older_than_seconds=300)
    assert eid in released
    assert queue.status_of(eid) == "pending"


# --- end-to-end: A5 defers -> A7 queue -> login fires ------------------ #
def test_a5_deferred_flows_into_queue_and_fires_on_login():
    engine = a6()
    q = PendingQueue(_bootstrapped())
    # A5 routes a deferred signal straight into the queue (PendingQueue is a DeferredSink)
    evaluator = TriggerEvaluator(engine, deferred_sink=q)
    ev = _repeated_search_event()
    assert evaluator.evaluate(ev).status.value == "deferred"
    assert q.count(CUST, "pending") == 1

    sched, fs = scheduler(q, engine=engine)
    msg = sched.on_login(CUST, T0 + timedelta(days=1))
    assert msg is not None and msg.trigger_id == "repeated_search_v1"


# --- helpers ------------------------------------------------------------ #
def _bootstrapped():
    engine = _sqlite()
    pending_bootstrap.create_all(engine)
    return engine


def _repeated_search_event():
    from generator.models import Event, EventContext
    return Event(
        event_id="e1", customer_id=CUST, session_id="s1",
        signal_type=SignalType.repeated_search, occurred_at=T0,
        context=EventContext(pickup="LHR", dropoff="LHR"),
    )
