"""Delivery transport to the Ingestion API (POA/01 §4.3).

The SDK posts batches to A4's ``/v1/events:batch``. Transport failures (network /
non-2xx) raise ``TransportError`` so the client keeps the batch buffered and
retries — no event loss under a transient outage (POA/01 §7). The HTTP call uses
stdlib urllib (no heavy dep, per §5) with an injectable poster so tests can drive
A4 through its TestClient.
"""
from __future__ import annotations

import json as _json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol

BATCH_PATH = "/v1/events:batch"


class TransportError(Exception):
    """Retryable delivery failure — the batch was not accepted."""


class Transport(Protocol):
    def send_batch(self, payload: dict) -> dict: ...


class InMemoryTransport:
    """Records sent batches; can fail N times to exercise buffering/retry."""

    def __init__(self, fail_times: int = 0) -> None:
        self.sent_batches: list[dict] = []
        self._fail_times = fail_times

    def send_batch(self, payload: dict) -> dict:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise TransportError("simulated ingestion outage")
        self.sent_batches.append(payload)
        return {"accepted": len(payload.get("events", []))}

    @property
    def sent_events(self) -> list[dict]:
        return [e for b in self.sent_batches for e in b.get("events", [])]


# poster: (url, payload, headers) -> (status_code, json_body)
Poster = Callable[[str, dict, dict], tuple[int, dict]]


class HttpTransport:
    def __init__(
        self, base_url: str, *, api_key: str | None = None,
        timeout: float = 5.0, post: Poster | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + BATCH_PATH
        self._timeout = timeout
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._post = post or self._urllib_post

    def send_batch(self, payload: dict) -> dict:
        status, body = self._post(self._url, payload, dict(self._headers))
        if not (200 <= status < 300):
            raise TransportError(f"ingestion rejected batch: status {status}")
        return body

    def _urllib_post(self, url: str, payload: dict, headers: dict) -> tuple[int, dict]:
        data = _json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode() or "{}"
                return resp.status, _json.loads(raw)
        except urllib.error.HTTPError as exc:
            return exc.code, {}
        except urllib.error.URLError:
            return 0, {}     # network failure -> treated as retryable
