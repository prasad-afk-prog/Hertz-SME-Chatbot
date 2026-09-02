"""Consistent error responses (M15 §2 error handling). Unhandled exceptions are
logged with the correlation id and returned as an RFC 7807-style problem object,
never as a stack trace to the caller.
"""
from __future__ import annotations

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from .logging import get_correlation_id, get_logger

_log = get_logger("platform.errors")


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _log.error(
        "unhandled_exception",
        extra={"extra_fields": {"path": request.url.path, "method": request.method}},
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "type": "about:blank",
            "title": "Internal Server Error",
            "status": 500,
            "correlation_id": get_correlation_id(),
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(Exception, unhandled_exception_handler)
