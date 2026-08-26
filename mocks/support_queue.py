"""Support/agent queue mock (backs M07). Returns a ticket ref, or fails to
exercise the retry/dead-letter path.
"""
from __future__ import annotations

import uuid


class SupportQueueMock:
    def __init__(self, fail: bool = False) -> None:
        self.tickets: list[dict] = []
        self._fail = fail

    def enqueue(self, payload: dict) -> str:
        if self._fail:
            raise RuntimeError("support queue unavailable")
        ref = f"ticket-{uuid.uuid4()}"
        self.tickets.append({"ref": ref, "payload": payload})
        return ref
