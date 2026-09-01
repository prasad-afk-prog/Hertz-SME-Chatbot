"""Correlation-id + access-log + metrics middleware (M15 §5).

Every request gets a correlation id (from the inbound header, or freshly minted),
bound to the logging context and echoed back on the response. Each request is
access-logged and recorded to the Prometheus HTTP metrics.
"""
from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from .logging import get_logger, set_correlation_id
from .metrics import observe_request


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, service_name: str, header: str = "X-Request-ID") -> None:
        super().__init__(app)
        self.service_name = service_name
        self.header = header
        self.log = get_logger("platform.access")

    async def dispatch(self, request: Request, call_next):
        cid = request.headers.get(self.header) or uuid.uuid4().hex
        set_correlation_id(cid)
        start = time.perf_counter()
        response = await call_next(request)
        duration_s = time.perf_counter() - start
        response.headers[self.header] = cid
        observe_request(self.service_name, request.method, response.status_code, duration_s)
        self.log.info(
            "request",
            extra={"extra_fields": {
                "service": self.service_name,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "dur_ms": round(duration_s * 1000, 2),
            }},
        )
        return response
