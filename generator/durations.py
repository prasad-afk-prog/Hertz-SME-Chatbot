"""Minimal ISO-8601 duration parsing (PnDTnHnMnS) used by trigger config.

Only the subset the trigger config needs is supported: days, hours, minutes,
seconds. Enough for wait periods, expiry windows and frequency-cap windows.
"""
from __future__ import annotations

import math
import re
from datetime import timedelta

_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def parse_duration(text: str) -> timedelta:
    """'P7D' -> 7 days, 'PT30M' -> 30 min, 'PT0S' -> 0. Raises on garbage."""
    m = _RE.match(text)
    if not m or text in ("P", "PT"):
        raise ValueError(f"unsupported duration: {text!r}")
    parts = {k: int(v) for k, v in m.groupdict().items() if v}
    return timedelta(
        days=parts.get("days", 0),
        hours=parts.get("hours", 0),
        minutes=parts.get("minutes", 0),
        seconds=parts.get("seconds", 0),
    )


def late_return_extra_days(overdue: timedelta, grace_minutes: int = 29) -> int:
    """Chargeable extra rental days for a late return (S2 — POA/16 §16.1).

    Within the grace period -> 0. Beyond it, each started 24-hour period counts
    as one extra rental day, so being 40 minutes late is 1 day and 26 hours late
    is 2. `overdue` is (actual_return - due_return); non-positive means on time.
    """
    if overdue <= timedelta(minutes=grace_minutes):
        return 0
    return math.ceil(overdue / timedelta(days=1))
