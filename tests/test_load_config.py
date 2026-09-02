"""S5 (POA/16 §16.3) — load/SLA/timeout knobs are present, match the resolved
spec, and are exposed for the soak/load tier.
"""
from __future__ import annotations

from dataclasses import asdict

from generator.config import GenConfig, LoadProfile
from generator.volume import VolumeSampler


def test_load_profile_defaults_match_spec():
    lp = LoadProfile()
    # POA/16 §16.3 resolved targets
    assert lp.events_per_sec == 10
    assert lp.peak_eps == 50
    assert lp.burst_eps == 100
    assert lp.concurrent_customers == 500
    assert lp.stress_customers == 1000
    assert lp.sla_standard_ms == 2000
    assert lp.sla_tool_ms == 5000
    assert lp.mock_timeout_ms == 5000


def test_sla_and_rate_ordering_is_sane():
    lp = LoadProfile()
    assert lp.sla_standard_ms < lp.sla_tool_ms          # no-call reply faster than tool-backed
    assert lp.events_per_sec < lp.peak_eps < lp.burst_eps
    assert lp.concurrent_customers < lp.stress_customers


def test_genconfig_carries_load_profile_and_one_way_share():
    cfg = GenConfig()
    assert isinstance(cfg.load, LoadProfile)
    assert 0.0 <= cfg.one_way_booking_share <= 1.0


def test_volume_sampler_exposes_load_summary(world):
    cfg = GenConfig(seed=42, n_customers=50)
    sampler = VolumeSampler(cfg, world)
    summary = sampler.load_summary()
    assert summary == asdict(cfg.load)
    assert summary["stress_customers"] == 1000
