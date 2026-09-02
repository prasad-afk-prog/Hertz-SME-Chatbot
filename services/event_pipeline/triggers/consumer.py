"""Redis Streams consumer (POA/04 §3.1/§5.1) — the prod loop that drives the
evaluator: XREADGROUP from ``events:in`` (group ``trigger-eval``), evaluate,
XACK. At-least-once from the stream; the evaluator's IdempotencyGuard makes
re-delivery safe. Exercised in the docker-compose integration environment.
"""
from __future__ import annotations

from typing import Any

from services.event_pipeline.store.publisher import CONSUMER_GROUP, STREAM

from .evaluator import TriggerEvaluator, parse_stream_fields


class RedisTriggerConsumer:
    def __init__(
        self,
        redis_client: Any,
        evaluator: TriggerEvaluator,
        *,
        stream: str = STREAM,
        group: str = CONSUMER_GROUP,
        consumer: str = "trigger-eval-1",
        batch: int = 100,
        block_ms: int = 5000,
    ) -> None:
        self._redis = redis_client
        self._evaluator = evaluator
        self._stream = stream
        self._group = group
        self._consumer = consumer
        self._batch = batch
        self._block_ms = block_ms

    def run_once(self) -> int:
        """Read and process one batch of new messages; returns how many. Blocks
        up to block_ms for new entries."""
        resp = self._redis.xreadgroup(
            self._group, self._consumer, {self._stream: ">"},
            count=self._batch, block=self._block_ms,
        )
        processed = 0
        for _stream, messages in resp or []:
            for msg_id, fields in messages:
                self._evaluator.evaluate(parse_stream_fields(fields))
                self._redis.xack(self._stream, self._group, msg_id)
                processed += 1
        return processed
