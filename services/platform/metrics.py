"""Prometheus metrics (M15 §5). A small set of shared HTTP metrics plus the
``/metrics`` scrape endpoint. Business/pipeline metrics are added by the
individual modules on top of this baseline.

Labels are deliberately low-cardinality (no raw request path) so the series
count stays bounded; route-templated metrics can be added per module later.
"""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

REQUESTS = Counter(
    "http_requests_total", "HTTP requests processed",
    ["service", "method", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency (seconds)",
    ["service", "method"],
)


def observe_request(service: str, method: str, status: int, duration_s: float) -> None:
    REQUESTS.labels(service, method, str(status)).inc()
    LATENCY.labels(service, method).observe(duration_s)


async def metrics_endpoint(_request: Request) -> Response:
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
