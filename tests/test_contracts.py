"""Contract tests: generated data is schema-valid by construction, and malformed
events are rejected (extra="forbid" / enum / required-field enforcement).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from generator.config import GenConfig
from generator.models import Event
from generator.volume import VolumeSampler


def test_volume_events_are_all_valid(world):
    _c, _b, _s, events = VolumeSampler(GenConfig(seed=42, n_customers=200), world).build()
    assert events, "expected some events"
    # re-validate every event against the contract model
    for e in events:
        Event.model_validate(e.model_dump())


def test_malformed_event_rejected_extra_field():
    with pytest.raises(ValidationError):
        Event.model_validate(
            {
                "event_id": "x",
                "customer_id": "c",
                "session_id": "s",
                "signal_type": "search_no_convert",
                "occurred_at": "2026-09-01T10:00:00Z",
                "surprise": "not allowed",  # extra -> forbidden
            }
        )


def test_malformed_event_rejected_bad_enum():
    with pytest.raises(ValidationError):
        Event.model_validate(
            {
                "event_id": "x",
                "customer_id": "c",
                "session_id": "s",
                "signal_type": "not_a_real_signal",
                "occurred_at": "2026-09-01T10:00:00Z",
            }
        )


def test_malformed_event_rejected_missing_field():
    with pytest.raises(ValidationError):
        Event.model_validate({"event_id": "x", "signal_type": "dormant"})
