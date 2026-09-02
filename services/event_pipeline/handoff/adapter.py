"""Queue adapter + dead-letter seams (POA/07 §3.3).

The support platform is unknown (POA/07 §10.1), so dispatch sits behind a
``QueueAdapter`` protocol — Zendesk/Salesforce/in-house drop in behind it. The
default wraps the existing SupportQueueMock. Dispatch failures that survive retry
go to a DeadLetterSink so a handoff is never lost silently (POA/07 §8).
"""
from __future__ import annotations

from typing import Any, Protocol


class QueueAdapter(Protocol):
    def enqueue(self, payload: dict) -> str: ...     # returns ticket_ref; raises on failure


class DeadLetterSink(Protocol):
    def record(self, payload: dict, error: str) -> None: ...


class MockQueueAdapter:
    """Wraps mocks.SupportQueueMock (or any object with enqueue(payload)->ref)."""

    def __init__(self, queue: Any) -> None:
        self._queue = queue

    def enqueue(self, payload: dict) -> str:
        return self._queue.enqueue(payload)


class InMemoryDeadLetterSink:
    def __init__(self) -> None:
        self.dead: list[tuple[dict, str]] = []

    def record(self, payload: dict, error: str) -> None:
        self.dead.append((payload, error))
