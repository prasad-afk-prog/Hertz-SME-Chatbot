"""Default config fixtures (M13): one trigger per signal type + handoff routing.
In-session by default; the two cross-session signals are deferred.
"""
from __future__ import annotations

from .models import (
    Deferred,
    FrequencyCap,
    RoutingRule,
    SignalType,
    TriggerConfig,
    TriggerMatch,
    TriggerType,
)

_DEFERRED = {SignalType.repeated_search, SignalType.dormant}
_PRECEDENCE = {
    SignalType.booking_abandoned: 200,   # closest to conversion -> highest
    SignalType.error_hit: 180,
    SignalType.rate_view_no_progress: 140,
    SignalType.extended_dwell: 120,
    SignalType.search_no_convert: 100,
    SignalType.session_ended_no_booking: 90,
    SignalType.repeated_search: 70,
    SignalType.dormant: 50,
}


def default_triggers() -> list[TriggerConfig]:
    triggers: list[TriggerConfig] = []
    for signal in SignalType:
        deferred = signal in _DEFERRED
        triggers.append(
            TriggerConfig(
                trigger_id=f"{signal.value}_v1",
                match=TriggerMatch(signal_type=signal),
                type=TriggerType.deferred if deferred else TriggerType.in_session,
                deferred=Deferred(wait_period="PT0S", expiry="P3D") if deferred else None,
                frequency_cap=FrequencyCap(per="P7D", max=1),
                precedence=_PRECEDENCE[signal],
                message_template_ref=f"tmpl_{signal.value}",
            )
        )
    return triggers


def default_routing_rules() -> list[RoutingRule]:
    return [
        RoutingRule(
            rule_id="de_corporate_v1",
            match={"language": "de", "customer_type": "corporate"},
            route={"queue": "de-corporate", "skill": "billing", "priority": "high"},
            sla={"first_response": "PT5M"},
            fallback_queue="general-de",
        ),
        RoutingRule(
            rule_id="en_default_v1",
            match={"language": "en"},
            route={"queue": "en-general", "priority": "normal"},
            fallback_queue="general",
        ),
        RoutingRule(
            rule_id="catch_all_v1",
            match={},
            route={"queue": "general", "priority": "normal"},
            fallback_queue="general",
        ),
    ]
