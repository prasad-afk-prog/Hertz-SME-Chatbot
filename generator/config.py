"""Generation configuration — distributions are documented, tunable assumptions
(design principle P7). With no real data these are explicit knobs, not magic
numbers. See POA/16 §7.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import CustomerType, SignalType


def _default_signal_mix() -> dict[SignalType, float]:
    return {
        SignalType.search_no_convert: 0.35,
        SignalType.rate_view_no_progress: 0.20,
        SignalType.booking_abandoned: 0.15,
        SignalType.error_hit: 0.05,
        SignalType.extended_dwell: 0.10,
        SignalType.session_ended_no_booking: 0.10,
        SignalType.repeated_search: 0.03,
        SignalType.dormant: 0.02,
    }


def _default_customer_type_mix() -> dict[CustomerType, float]:
    return {
        CustomerType.individual: 0.50,
        CustomerType.SME: 0.35,
        CustomerType.corporate: 0.15,
    }


def _default_region_language() -> dict[str, float]:
    # region/language -> weight
    return {"UK/en": 0.6, "DE/de": 0.2, "FR/fr": 0.1, "ES/es": 0.1}


@dataclass
class GenConfig:
    seed: int = 42

    # volume tier sizing
    n_customers: int = 500
    max_sessions_per_customer: int = 4

    # distributions (assumptions — tune against reality later)
    signal_mix: dict = field(default_factory=_default_signal_mix)
    customer_type_mix: dict = field(default_factory=_default_customer_type_mix)
    region_language: dict = field(default_factory=_default_region_language)

    # funnel (drives M12/M14)
    response_rate: float = 0.25
    bot_resolve_rate: float = 0.60
    conversion_after_resolve: float = 0.45

    # claim mix (of delivered messages)
    makes_claim: float = 0.40
    of_claims_wrong: float = 0.15
    of_claims_unverifiable: float = 0.05

    # behaviour shape
    session_events_lambda: float = 6.0
    dwell_mu: float = 10.5
    dwell_sigma: float = 0.8
    extended_dwell_threshold_ms: int = 60_000

    # dormancy threshold (signal J)
    dormancy_days: int = 90
