"""Deterministic precedence arbitration (POA/05 §3.3).

Winner among simultaneous matches: highest `precedence` weight, then the most
specific match (more match conditions), then the most recent signal, then the
lowest trigger id as a stable final tiebreak. Fully deterministic — no reliance
on input order.
"""
from __future__ import annotations

from generator.models import MatchCandidate


def _sort_key(c: MatchCandidate) -> tuple:
    # ascending sort; negate the "higher wins" fields, keep trigger_id ascending.
    return (
        -c.trigger.precedence,
        -len(c.trigger.match.conditions),
        -c.signal_at.timestamp(),
        c.trigger.trigger_id,
    )


def choose_winner(candidates: list[MatchCandidate]) -> MatchCandidate:
    if not candidates:
        raise ValueError("no candidates to arbitrate")
    return sorted(candidates, key=_sort_key)[0]
