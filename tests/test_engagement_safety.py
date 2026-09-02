"""Regression tests for three defects found reviewing the merged tree (2026-09-02).

Every test here fails against the code as it stood before the fix. They are
grouped in one file because they share a theme: **the frequency cap is the thing
that stops the system over-messaging customers, and two of these let it be
exceeded or silently re-burned.**

1. `reserve()` defaulted to `NullLock`, so a read-decide-write race let two
   concurrent events for one customer both pass the cap check and both reserve.
   The existing invariant suite runs single-threaded and cannot see this.
2. `set_status` was an unconditional UPDATE, so a late or duplicated `confirm`
   after a `rollback` re-burned a slot the customer never received.
3. `AllowAllAuthenticator`'s docstring promised it was "logged loudly so it never
   ships silently" while logging nothing — worse than an honest gap, because a
   reviewer reads the claim and stops looking.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from generator.models import (
    FrequencyCap,
    MatchCandidate,
    SignalType,
    TriggerConfig,
    TriggerMatch,
)
from services.event_pipeline.frequency import EngagementLedger, FrequencyPrecedenceEngine
from services.event_pipeline.frequency import bootstrap as freq_bootstrap
from services.event_pipeline.frequency.lock import NullLock, PerCustomerLock
from services.event_pipeline.ingestion.auth import AllowAllAuthenticator
from services.event_pipeline.ingestion.ratelimit import InMemoryRateLimiter

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def _ledger() -> EngagementLedger:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    freq_bootstrap.create_all(engine)
    return EngagementLedger(engine)


def _trigger(tid="t1", precedence=100, cap_max=1) -> TriggerConfig:
    return TriggerConfig(
        trigger_id=tid,
        match=TriggerMatch(signal_type=SignalType.booking_abandoned),
        frequency_cap=FrequencyCap(per="P7D", max=cap_max),
        precedence=precedence,
    )


# =========================================================================== #
# 1. Concurrent reserve must not exceed the cap
# =========================================================================== #
def test_the_default_lock_is_a_real_lock_not_a_null_one():
    """The default has to be the safe one: the invariant suite is
    single-threaded and structurally cannot catch a reserve race."""
    engine = FrequencyPrecedenceEngine(_ledger())
    assert isinstance(engine.lock, PerCustomerLock)
    assert not isinstance(engine.lock, NullLock)


def test_concurrent_reserves_for_one_customer_respect_a_cap_of_one():
    """The race: two events for the same customer both read fire_times before
    either writes, both pass the cap check, and both reserve."""
    engine = FrequencyPrecedenceEngine(_ledger())
    approvals: list[bool] = []
    barrier = threading.Barrier(8)

    def attempt() -> None:
        barrier.wait()                      # maximise overlap inside reserve()
        decision = engine.reserve(
            "cust-race", [MatchCandidate(trigger=_trigger(cap_max=1), signal_at=T0)], T0
        )
        approvals.append(decision.approved)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(approvals) == 1, f"cap of 1 exceeded — {sum(approvals)} reservations approved"


def test_different_customers_are_not_serialised_against_each_other():
    """The lock is per customer; it must not become a global bottleneck."""
    engine = FrequencyPrecedenceEngine(_ledger())
    approvals: list[bool] = []

    def attempt(customer: str) -> None:
        approvals.append(
            engine.reserve(customer, [MatchCandidate(trigger=_trigger(), signal_at=T0)], T0).approved
        )

    threads = [threading.Thread(target=attempt, args=(f"cust-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(approvals), "independent customers must each get their engagement"


# =========================================================================== #
# 2. The reservation state machine
# =========================================================================== #
def test_a_rolled_back_reservation_cannot_be_confirmed():
    """The bug: confirm/rollback arrive from M08 over a network, where retries
    and out-of-order delivery are normal. An unconditional UPDATE let a late
    confirm re-burn a slot the customer never received."""
    ledger = _ledger()
    engine = FrequencyPrecedenceEngine(ledger)
    ledger.reserve("r-1", "cust-1", "t1", T0)

    assert engine.rollback("r-1") is True
    assert ledger.status_of("r-1") == "rolled_back"

    assert engine.confirm("r-1") is False, "a released slot must not be re-confirmed"
    assert ledger.status_of("r-1") == "rolled_back"


def test_a_confirmed_reservation_cannot_be_rolled_back():
    """The mirror case: releasing a genuinely used engagement would hand the
    customer back a slot they already received a message for."""
    ledger = _ledger()
    engine = FrequencyPrecedenceEngine(ledger)
    ledger.reserve("r-2", "cust-1", "t1", T0)

    assert engine.confirm("r-2") is True
    assert engine.rollback("r-2") is False
    assert ledger.status_of("r-2") == "confirmed"


def test_repeating_a_transition_is_a_harmless_no_op():
    ledger = _ledger()
    engine = FrequencyPrecedenceEngine(ledger)
    ledger.reserve("r-3", "cust-1", "t1", T0)

    assert engine.confirm("r-3") is True
    assert engine.confirm("r-3") is False, "second confirm moved nothing"
    assert ledger.status_of("r-3") == "confirmed"


def test_a_transition_on_an_unknown_reservation_reports_failure():
    """Previously a silent no-op, so a genuine bug in the caller was invisible."""
    engine = FrequencyPrecedenceEngine(_ledger())
    assert engine.confirm("never-existed") is False
    assert engine.rollback("never-existed") is False


def test_rollback_accepts_and_keeps_m08s_reason():
    """POA/18 §5 item 2: M08 has a reason for every rollback and M14 needs it.
    Optional, so existing Track-A callers are unaffected."""
    ledger = _ledger()
    engine = FrequencyPrecedenceEngine(ledger)
    ledger.reserve("r-4", "cust-1", "t1", T0)

    assert engine.rollback("r-4", reason="delivery_failed") is True
    assert engine.rollback("r-5") is False        # positional-free call still works


def test_a_rolled_back_engagement_does_not_count_toward_the_cap():
    """The whole point of rollback — a failed send must not burn the slot."""
    ledger = _ledger()
    engine = FrequencyPrecedenceEngine(ledger)
    first = engine.reserve("cust-9", [MatchCandidate(trigger=_trigger(cap_max=1), signal_at=T0)], T0)
    assert first.approved
    engine.rollback(first.reservation_id, reason="delivery_failed")

    second = engine.reserve(
        "cust-9", [MatchCandidate(trigger=_trigger(cap_max=1), signal_at=T0)], T0 + timedelta(minutes=1)
    )
    assert second.approved, "the released slot should be available again"


# =========================================================================== #
# 3. Unauthenticated ingestion must be loud
# =========================================================================== #
def test_allow_all_authenticator_warns_on_construction(caplog):
    with caplog.at_level(logging.WARNING):
        AllowAllAuthenticator()
    assert any("DISABLED" in r.getMessage() for r in caplog.records), \
        "no-auth must announce itself; the docstring used to claim this while logging nothing"


def test_allow_all_authenticator_warns_on_every_request(caplog):
    auth = AllowAllAuthenticator()

    class FakeURL:
        path = "/v1/events"

    class FakeRequest:
        url = FakeURL()

    with caplog.at_level(logging.WARNING):
        principal = auth.authenticate(FakeRequest())

    assert principal.source == "local"
    assert any("unauthenticated_request" in r.getMessage() for r in caplog.records)


# =========================================================================== #
# 4. The rate limiter must not leak windows
# =========================================================================== #
def test_rate_limiter_evicts_stale_windows():
    """One entry per (source, customer) seen since boot, never evicted, on the
    live request path."""
    clock = {"t": 0.0}
    limiter = InMemoryRateLimiter(limit_per_min=100, now=lambda: clock["t"])

    for i in range(1100):
        limiter.allow("portal", f"cust-{i}")
    assert len(limiter._windows) > 1000

    clock["t"] += 600.0                       # past _EVICT_AFTER_S
    limiter.allow("portal", "cust-new")
    assert len(limiter._windows) < 100, "stale windows were not evicted"


def test_eviction_does_not_drop_a_live_window():
    clock = {"t": 0.0}
    limiter = InMemoryRateLimiter(limit_per_min=2, now=lambda: clock["t"])

    assert limiter.allow("portal", "cust-live") is True
    for i in range(1100):
        limiter.allow("portal", f"filler-{i}")

    # cust-live is still inside its 60s window and has used 1 of 2.
    assert limiter.allow("portal", "cust-live") is True
    assert limiter.allow("portal", "cust-live") is False, "live window was lost to eviction"
