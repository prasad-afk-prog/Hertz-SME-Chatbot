"""LLM provider mock (backs M09).

Returns the scenario's fixture LLMResponse, or raises LLMTimeout when the
scenario forces provider-unavailability. A real provider adapter implements the
same `generate` shape; the M09 confidence/fallback decision lives in
generator.reference.decide_llm.
"""
from __future__ import annotations

from generator.models import LLMResponse


class LLMTimeout(Exception):
    """Provider timed out / errored — M09 must fall back."""


class LLMProviderMock:
    def __init__(self, response: LLMResponse | None, timeout: bool = False) -> None:
        self._response = response
        self._timeout = timeout

    def generate(self) -> LLMResponse:
        if self._timeout or self._response is None:
            raise LLMTimeout("forced timeout")
        return self._response
