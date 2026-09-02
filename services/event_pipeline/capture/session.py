"""Session correlation (POA/01 §3, §4). A stable ``session_id`` stitches a
customer's events into one session; a new login starts a new session.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass
class Session:
    session_id: str
    customer_id: str


def new_session(customer_id: str, session_id: str | None = None) -> Session:
    return Session(session_id or f"sess-{uuid.uuid4().hex[:12]}", customer_id)
