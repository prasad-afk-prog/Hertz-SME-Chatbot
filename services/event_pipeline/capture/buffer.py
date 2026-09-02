"""Bounded offline buffer (POA/01 §4.3) — holds captured events until a flush
succeeds, so nothing is lost while the Ingestion API is unreachable. Capped size
(oldest dropped past the cap) keeps memory bounded on the client.
"""
from __future__ import annotations

from collections import deque

from generator.models import Event


class EventBuffer:
    def __init__(self, max_size: int = 1000) -> None:
        self._items: deque[Event] = deque(maxlen=max_size)

    def add(self, event: Event) -> None:
        self._items.append(event)

    def snapshot(self) -> list[Event]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)
