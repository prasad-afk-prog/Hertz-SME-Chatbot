"""Per-customer lock seam (POA/05 §3.2) for the atomic check-and-reserve.

Default is an in-process ``threading`` lock per customer — enough to keep
concurrent reserves for one customer race-free within a worker. Production uses a
distributed lock (Redis SETNX / Redlock or a Postgres advisory lock) behind this
same call-as-context-manager interface (POA/05 §8).
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class PerCustomerLock:
    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _for(self, customer_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(customer_id, threading.Lock())

    @contextmanager
    def __call__(self, customer_id: str) -> Iterator[None]:
        lock = self._for(customer_id)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()


class NullLock:
    """No-op lock — single-threaded contexts / tests that don't need serialisation."""

    @contextmanager
    def __call__(self, customer_id: str) -> Iterator[None]:
        yield
