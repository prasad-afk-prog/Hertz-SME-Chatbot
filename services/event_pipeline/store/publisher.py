"""Stream publishers (POA/03 §3.3) — the outbox relay's delivery target.

The relay depends only on the ``StreamPublisher`` protocol, so the store's
correctness is tested against ``InMemoryStreamPublisher`` (no Redis needed) and
runs in prod against ``RedisStreamPublisher`` (XADD to ``events:in``).
"""
from __future__ import annotations

from typing import Any, Protocol

STREAM = "events:in"
CONSUMER_GROUP = "trigger-eval"          # M04 (A5) consumer group


class StreamPublisher(Protocol):
    def publish(self, event_id: str, fields: dict[str, str]) -> None: ...


class InMemoryStreamPublisher:
    """Records published messages; can be told to fail N times to exercise the
    relay's at-least-once retry path."""

    def __init__(self, fail_times: int = 0) -> None:
        self.messages: list[tuple[str, dict[str, str]]] = []
        self._fail_times = fail_times

    def publish(self, event_id: str, fields: dict[str, str]) -> None:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("simulated stream outage")
        self.messages.append((event_id, fields))

    @property
    def event_ids(self) -> list[str]:
        return [eid for eid, _ in self.messages]


class RedisStreamPublisher:
    """Real publisher: XADD to a Redis Stream. Fields must be str/bytes."""

    def __init__(self, redis_client: Any, stream: str = STREAM, maxlen: int = 1_000_000) -> None:
        self._redis = redis_client
        self._stream = stream
        self._maxlen = maxlen

    def publish(self, event_id: str, fields: dict[str, str]) -> None:
        # approximate maxlen trimming keeps the stream bounded (POA/03 §3.3)
        self._redis.xadd(self._stream, fields, maxlen=self._maxlen, approximate=True)

    def ensure_group(self, group: str = CONSUMER_GROUP) -> None:
        """Create the consumer group (and the stream) if absent — idempotent."""
        try:
            self._redis.xgroup_create(self._stream, group, id="0", mkstream=True)
        except Exception as exc:  # noqa: BLE001 - redis raises BUSYGROUP when it already exists
            if "BUSYGROUP" not in str(exc):
                raise
