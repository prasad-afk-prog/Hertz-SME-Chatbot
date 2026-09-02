"""Source authentication + identity binding (POA/02 §2, §3.3, §10.1/§10.3).

The portal->API mechanism is an open question, so authentication sits behind a
``SourceAuthenticator`` protocol: today a shared-secret API key, with mTLS or a
signed/JWT token dropping in behind the same seam. A JWT variant would set
``Principal.customer_id`` so identity binding (don't trust the body's customer_id
blindly) becomes a real cross-check rather than trusted-source acceptance.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, Request, status

from services.platform import get_logger

log = get_logger("ingestion.auth")


@dataclass
class Principal:
    source: str
    customer_id: str | None = None    # set only when the source carries a verified identity


class SourceAuthenticator(Protocol):
    def authenticate(self, request: Request) -> Principal: ...


class AllowAllAuthenticator:
    """Local/dev only — no authentication.

    Warns on construction AND on every request. The docstring used to promise
    "logged loudly so it never ships silently" while logging nothing at all,
    which is worse than an honest gap: a reviewer reads the claim and stops
    looking. Unauthenticated ingestion is the spoofed-event risk in POA/02 §3.3,
    so the warning has to be real.
    """

    def __init__(self) -> None:
        log.warning(
            "ingestion.auth.DISABLED — AllowAllAuthenticator accepts every request "
            "unauthenticated. Local/dev only; never deploy this."
        )

    def authenticate(self, request: Request) -> Principal:
        log.warning(
            "ingestion.auth.unauthenticated_request",
            extra={"path": str(request.url.path)},
        )
        return Principal(source="local")


class ApiKeyAuthenticator:
    """Shared-secret bearer / X-API-Key authenticator (constant-time compare)."""

    def __init__(self, expected_key: str, source: str = "portal") -> None:
        self._key = expected_key
        self._source = source

    def authenticate(self, request: Request) -> Principal:
        provided = _bearer(request) or request.headers.get("x-api-key")
        if not provided or not hmac.compare_digest(provided, self._key):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key")
        return Principal(source=self._source)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else None


def identity_conflict(principal: Principal, event_customer_id: str) -> bool:
    """True when the source carries a verified identity that disagrees with the
    body's customer_id (POA/02 §3.3 — spoofed-customer_id guard)."""
    return principal.customer_id is not None and principal.customer_id != event_customer_id
