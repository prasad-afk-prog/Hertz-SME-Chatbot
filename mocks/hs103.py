"""HS-103 chat UI mock (backs M11).

Captures delivered messages and replays a scripted set of customer replies as
inbound turns. `fail_delivery` drives the delivery-failure branch.
"""
from __future__ import annotations

import uuid


class HS103Mock:
    def __init__(self, replies: list[str] | None = None, fail_delivery: bool = False) -> None:
        self.delivered: list[str] = []
        self._replies = list(replies or [])
        self._fail = fail_delivery

    def deliver(self, conversation_id: str, text: str) -> str:
        if self._fail:
            raise RuntimeError("HS-103 delivery failed")
        self.delivered.append(text)
        return f"delivery-{uuid.uuid4()}"

    def inbound(self) -> list[str]:
        """The customer replies (empty list = no response -> AF)."""
        return list(self._replies)

    @property
    def responded(self) -> bool:
        return bool(self._replies)
