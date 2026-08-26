"""Mocked external systems for testing (all read the SAME seeded world).

  * BookingAPIMock   — rate/availability + claim verification (M10 dependency)
  * LLMProviderMock  — deterministic fixture responses + failure modes (M09)
  * HS103Mock        — delivery + inbound replies (M11)
  * SupportQueueMock — handoff dispatch (M07)

Each mock can be flipped between happy path and a named failure mode per
scenario, so the negative branches are driven by DATA, not code changes.
"""
from .booking_api import BookingAPIMock, BookingAPIFailure
from .hs103 import HS103Mock
from .llm_provider import LLMProviderMock, LLMTimeout
from .support_queue import SupportQueueMock

__all__ = [
    "BookingAPIMock",
    "BookingAPIFailure",
    "LLMProviderMock",
    "LLMTimeout",
    "HS103Mock",
    "SupportQueueMock",
]
