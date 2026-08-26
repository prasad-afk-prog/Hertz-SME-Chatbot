"""Aggregate/invariant tests over the volume tier (Tier B) — the kind of
assertion you make when there is no per-record expected outcome (POA/16 §6).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from generator.config import GenConfig
from generator.models import FrequencyCap
from generator.reference import would_fire
from generator.volume import VolumeSampler
from tests.runner import run_scenario


def test_frequency_cap_never_exceeded_over_volume(world):
    """Enforcing a 1-per-7-day cap yields no 7-day window with >1 fire, and the
    data actually exercises suppression (proves the cap does something)."""
    cfg = GenConfig(seed=42, n_customers=300)
    _c, _b, _s, events = VolumeSampler(cfg, world).build()
    cap = FrequencyCap(per="P7D", max=1)  # global per-customer for this invariant

    fires: dict[str, list] = defaultdict(list)
    suppressed = 0
    for e in sorted(events, key=lambda e: e.occurred_at):
        if would_fire(fires[e.customer_id], e.occurred_at, cap):
            fires[e.customer_id].append(e.occurred_at)
        else:
            suppressed += 1

    # invariant: no customer has >max fires inside any 7-day window
    window = timedelta(days=7)
    for times in fires.values():
        times.sort()
        for i, t0 in enumerate(times):
            in_window = [t for t in times[i:] if t - t0 < window]
            assert len(in_window) <= cap.max

    assert suppressed > 0, "volume data did not exercise the frequency cap"


def test_no_unverified_claim_ever_delivered_across_golden(scenarios, world):
    """The global trust invariant: across every golden scenario, no forbidden
    (wrong/unverifiable) token survives into the delivered message."""
    leaks = []
    for sc in scenarios:
        res = run_scenario(sc, world)
        if res.delivered is None:
            continue
        for forbidden in sc.expected.delivered_excludes:
            if forbidden in res.delivered:
                leaks.append((sc.scenario_id, forbidden))
    assert not leaks, f"unverified claims leaked: {leaks}"
