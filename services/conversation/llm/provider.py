"""Provider adapter + resilience wrapper (M09 node Y) — POA/09 §3.1, §3.4.

`LLMProvider` is the swap point POA/09 §6 requires: "provider/model is
switchable via config with no code change". `MockProviderAdapter` puts the
existing `mocks/llm_provider.py` behind it today; `AnthropicProvider` slots in
behind the same protocol once §10.1 (provider, model, hosting region for Hertz
data residency) is answered.

**Sync, not async — a recorded deviation from §3.1.** The spec shows
`async def generate`. Everything else in this repo is synchronous, and going
async would pull `pytest-asyncio` into `requirements-dev.txt` — a shared file,
with Prasad's S5 load work the other likely claimant. The protocol is otherwise
shape-for-shape identical, so the migration is mechanical when a real SDK lands.
See POA/09 §11.

`RetryingProvider` supplies §3.4's timeouts, bounded retries and circuit
breaker. Both the clock and the backoff sleep are injected, so retry behaviour
is asserted by *count and bound* rather than by wall-clock timing — a flaky
test in a sub-second suite is worse than no test.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

from generator.models import LLMResponse
from mocks.llm_provider import LLMProviderMock, LLMTimeout
from services.common.resilience import CircuitBreaker, Clock


class ProviderError(Exception):
    """The provider could not produce a draft. Always ends in fallback (X)."""


class ProviderTimeout(ProviderError):
    """Generation exceeded the latency budget."""


class ProviderUnavailable(ProviderError):
    """Outage, auth failure, or the circuit is open."""


@dataclass
class Usage:
    """Cost/latency telemetry (§2, §5.6)."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResult:
    """What a provider returns (§3.1). Wraps the contract `LLMResponse` so
    provider-specific telemetry does not leak into the shared models."""
    response: LLMResponse
    usage: Usage = field(default_factory=Usage)
    safety_flags: list[str] = field(default_factory=list)
    attempts: int = 1


@runtime_checkable
class LLMProvider(Protocol):
    def generate(self, prompt: str, *, timeout: float) -> LLMResult: ...

    @property
    def model_id(self) -> str: ...


class MockProviderAdapter:
    """Puts `mocks.llm_provider.LLMProviderMock` behind `LLMProvider`.

    `latency_s` is a *simulated* duration, not a real delay — it drives the
    timeout path deterministically, with nothing ever sleeping.
    """

    def __init__(
        self,
        mock: LLMProviderMock,
        model_id: str = "mock-model-v1",
        latency_s: Callable[[], float] | float = 0.0,
        prompt_tokens: int = 120,
        completion_tokens: int = 40,
    ) -> None:
        self._mock = mock
        self._model_id = model_id
        self._latency = latency_s if callable(latency_s) else (lambda: float(latency_s))
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(self, prompt: str, *, timeout: float) -> LLMResult:
        latency = self._latency()
        if latency > timeout:
            raise ProviderTimeout(f"generation exceeded {timeout}s budget")
        try:
            response = self._mock.generate()
        except LLMTimeout as exc:
            raise ProviderUnavailable(str(exc)) from exc
        return LLMResult(
            response=response,
            usage=Usage(
                prompt_tokens=self._prompt_tokens,
                completion_tokens=self._completion_tokens,
                latency_s=latency,
            ),
        )


class RetryingProvider:
    """§3.4 — timeout, bounded retries, circuit breaker.

    A timeout or outage is retried up to `max_retries`; anything else is not,
    because retrying a deterministic failure only burns the latency budget. When
    the breaker is open we go straight to fallback without calling the provider
    at all.

    `sleep` is injected and defaults to a no-op, so retries cost nothing in
    tests. Jitter is deliberately NOT implemented — see POA/09 §11; bounded
    retries without jitter are honest, whereas untestable jitter is not.
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        max_retries: int = 2,
        backoff_s: float = 0.25,
        breaker_threshold: int = 3,
        breaker_cooldown_s: float = 30.0,
        clock: Clock = time.monotonic,
        sleep: Callable[[float], None] = lambda _s: None,
    ) -> None:
        self.inner = inner
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.sleep = sleep
        self.breaker = CircuitBreaker(
            threshold=breaker_threshold, cooldown_s=breaker_cooldown_s, clock=clock
        )
        self.attempts_made = 0

    @property
    def model_id(self) -> str:
        return self.inner.model_id

    def generate(self, prompt: str, *, timeout: float) -> LLMResult:
        if self.breaker.is_open:
            raise ProviderUnavailable("circuit open — going straight to fallback")

        last: ProviderError | None = None
        for attempt in range(1, self.max_retries + 2):     # 1 try + N retries
            self.attempts_made = attempt
            try:
                result = self.inner.generate(prompt, timeout=timeout)
            except (ProviderTimeout, ProviderUnavailable) as exc:
                last = exc
                self.breaker.record_failure()
                if attempt <= self.max_retries:
                    self.sleep(self.backoff_s * attempt)
                continue
            self.breaker.record_success()
            result.attempts = attempt
            return result

        raise last if last else ProviderUnavailable("exhausted retries")
