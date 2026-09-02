"""Outbox relay (POA/03 §3.2) — drains unpublished outbox rows onto the stream.

Ordering is the whole correctness argument: publish FIRST, then mark published.
A crash between the two re-publishes the row on the next run (at-least-once); the
reverse order would silently lose events. Consumers dedupe on ``event_id`` for
the exactly-once effect. Each row is marked immediately after its own publish, so
a mid-batch failure never re-publishes rows already delivered.

In prod this runs as a loop/Celery beat; the tests drive ``run_once`` directly.
"""
from __future__ import annotations

import json
from typing import Any

from .publisher import StreamPublisher
from .store import SqlEventStore


def _stream_fields(payload: dict[str, Any]) -> dict[str, str]:
    """Flatten an event payload into Redis-stream fields (POA/03 §3.3)."""
    return {
        "event_id": str(payload["event_id"]),
        "customer_id": str(payload["customer_id"]),
        "signal_type": str(payload["signal_type"]),
        "session_id": str(payload["session_id"]),
        "occurred_at": str(payload["occurred_at"]),
        "context": json.dumps(payload.get("context") or {}),
    }


class OutboxRelay:
    def __init__(self, store: SqlEventStore, publisher: StreamPublisher, batch: int = 100) -> None:
        self.store = store
        self.publisher = publisher
        self.batch = batch

    def run_once(self) -> int:
        """Publish one batch of pending rows. Returns how many were published."""
        published = 0
        for row in self.store.unpublished_outbox(self.batch):
            self.publisher.publish(row["event_id"], _stream_fields(row["payload"]))
            self.store.mark_published([row["id"]])   # only after a successful publish
            published += 1
        return published
