"""Minimal ISO-8601 duration parsing (PnDTnHnMnS) used by trigger config.

Only the subset the trigger config needs is supported: days, hours, minutes,
seconds. Enough for wait periods, expiry windows and frequency-cap windows.
"""
from __future__ import annotations

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
