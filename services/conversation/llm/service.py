"""LLM Integration & Fallback Service (M09) — POA/09, flow nodes W, X, Y.

    generate (Y)  ->  available & confident? (W)  ->  use draft, or fallback (X)

This is the resilience gate on generation: **an error never reaches the
customer**. Provider down, too slow, empty, refusing, off-scope, or simply not
confident — every one of those ends in a safe localised templated message.

**The confidence threshold delegates to `generator.reference.decide_llm`.** That
function is the executable spec of the W decision and `test_golden_scenarios.py`
already asserts against it via GS-06 (LLM timeout -> localised German
fallback). Reimplementing the threshold here would mean that scenario stopped
covering what ships. The heuristics this module adds — refusal detection,
off-scope detection, length guardrails — layer *around* that call rather than
replacing it.

Two things this module deliberately does not do: it does not build the prompt
(M08) and it does not verify factual claims (M10). A draft that survives W still
goes to M10 before delivery — POA/09 §8 names "hallucinated offers slip through"
and the mitigation is mandatory downstream verification, not cleverness here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from generator.models import LLMResponse, MessageKind, SignalType
from generator.reference import decide_llm

from services.conversation.claim_verification.detection import mentions_money

from .fallback import FallbackCatalogue, RenderedFallback
from .provider import (
    LLMProvider,
    LLMResult,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    Usage,
)


class FallbackReason(str, Enum):
    """Why we fell back. Recorded per §3.2 ('with reason recorded')."""
    none = "none"
    provider_timeout = "provider_timeout"
    provider_unavailable = "provider_unavailable"
    empty_response = "empty_response"
    low_confidence = "low_confidence"
    refusal = "refusal"
    off_scope = "off_scope"
    too_long = "too_long"
    safety_flagged = "safety_flagged"


# Heuristics for §3.2. Deliberately conservative: POA/09 §8 says "prefer
# fallback when unsure", because a safe templated message is always acceptable
# whereas a bad generation is not.
_REFUSAL_MARKERS = (
    "i can't help", "i cannot help", "i'm unable to", "i am unable to",
    "as an ai", "i don't have access", "i do not have access",
    "i'm sorry, but i can", "cannot assist",
)
_ON_SCOPE_MARKERS = (
    "book", "booking", "rental", "rent", "hire", "vehicle", "car", "van",
    "pickup", "pick-up", "drop-off", "dropoff", "collect", "return",
    "rate", "price", "quote", "availability", "available", "reservation",
    "licence", "license", "driver", "insurance", "extras", "location",
    "help", "assist",
)


@dataclass
class LLMConfig:
    """The §5.7 config surface. Switching provider or model is a config change,
    never a code change (§6)."""
    provider: str = "mock"
    model_id: str = "mock-model-v1"
    temperature: float = 0.3
    max_tokens: int = 400
    timeout_s: float = 3.0
    max_retries: int = 2
    confidence_threshold: float = 0.5
    max_response_chars: int = 600
    prompt_version: str = "v1"


@dataclass
class GenerationRecord:
    """Per-generation telemetry (§2, §6: 'every generation logs prompt version,
    decision, tokens, latency')."""
    prompt_version: str
    model_id: str
    decision: str                       # "use_llm" | "use_fallback"
    reason: FallbackReason = FallbackReason.none
    usage: Usage = field(default_factory=Usage)
    attempts: int = 0
    confidence: float | None = None
    locale: str = "en"
    locale_missing: bool = False

    @property
    def used_fallback(self) -> bool:
        return self.decision == "use_fallback"


@dataclass
class GenerationOutcome:
    """What M08 gets back."""
    text: str
    message_kind: MessageKind
    response: LLMResponse | None          # None when we fell back
    record: GenerationRecord
    fallback: RenderedFallback | None = None

    @property
    def claims(self):
        """Claims for M10 to verify. A fallback asserts nothing, by design."""
        return list(self.response.claims) if self.response else []


class LLMService:
    def __init__(
        self,
        provider: LLMProvider,
        config: LLMConfig | None = None,
        catalogue: FallbackCatalogue | None = None,
    ) -> None:
        self.provider = provider
        self.config = config or LLMConfig()
        self.catalogue = catalogue or FallbackCatalogue()

    # ---- W: availability & confidence ---------------------------------- #
    def assess(self, response: LLMResponse | None, safety_flags: list[str] | None = None) -> FallbackReason:
        """Return `none` to use the draft, or the reason to fall back.

        The threshold decision itself is `reference.decide_llm`; everything here
        is an additional, more conservative gate on top of it.
        """
        if safety_flags:
            return FallbackReason.safety_flagged

        # Delegated: unavailable / empty / below-threshold.
        if decide_llm(response, threshold=self.config.confidence_threshold) == "fallback":
            if response is None:
                return FallbackReason.provider_unavailable
            if not response.text.strip():
                return FallbackReason.empty_response
            return FallbackReason.low_confidence

        assert response is not None       # decide_llm returned "use"
        text = response.text
        lowered = text.lower()

        if any(marker in lowered for marker in _REFUSAL_MARKERS):
            return FallbackReason.refusal
        if len(text) > self.config.max_response_chars:
            return FallbackReason.too_long

        # A message quoting a price IS on-scope by definition — the only thing
        # this assistant quotes prices about is rental. Without this, a perfectly
        # good reply like "It's £52.21/day." is scored off-scope because it
        # happens to contain none of the keywords, and the customer gets a
        # generic fallback instead of an answer. (It still goes through M10.)
        if mentions_money(text):
            return FallbackReason.none

        if not any(marker in lowered for marker in _ON_SCOPE_MARKERS):
            return FallbackReason.off_scope

        return FallbackReason.none

    # ---- Y -> W -> X ---------------------------------------------------- #
    def generate(
        self,
        prompt: str,
        *,
        signal: SignalType | None = None,
        locale: str = "en",
        context: dict[str, str] | None = None,
    ) -> GenerationOutcome:
        result: LLMResult | None = None
        reason = FallbackReason.none

        try:
            result = self.provider.generate(prompt, timeout=self.config.timeout_s)
        except ProviderTimeout:
            reason = FallbackReason.provider_timeout
        except (ProviderUnavailable, ProviderError):
            reason = FallbackReason.provider_unavailable

        if reason is FallbackReason.none and result is not None:
            reason = self.assess(result.response, result.safety_flags)

        record = GenerationRecord(
            prompt_version=self.config.prompt_version,
            model_id=self.provider.model_id,
            decision="use_llm" if reason is FallbackReason.none else "use_fallback",
            reason=reason,
            usage=result.usage if result else Usage(),
            attempts=result.attempts if result else getattr(self.provider, "attempts_made", 0),
            confidence=result.response.confidence if result else None,
            locale=locale,
        )

        if reason is FallbackReason.none and result is not None:
            return GenerationOutcome(
                text=result.response.text,
                message_kind=MessageKind.llm,
                response=result.response,
                record=record,
            )

        rendered = self.catalogue.render(signal, locale, context)
        record.locale = rendered.locale
        record.locale_missing = rendered.locale_missing
        return GenerationOutcome(
            text=rendered.text,
            message_kind=MessageKind.fallback,
            response=None,
            record=record,
            fallback=rendered,
        )


def build_provider(config: LLMConfig, **kwargs):
    """Provider selection from config alone (§6: switchable with no code change).

    `AnthropicProvider` is registered here once §10.1 answers which model and
    hosting region Hertz's data-residency rules allow.
    """
    if config.provider == "mock":
        from mocks.llm_provider import LLMProviderMock

        from .provider import MockProviderAdapter

        return MockProviderAdapter(
            kwargs.get("mock") or LLMProviderMock(None),
            model_id=config.model_id,
            latency_s=kwargs.get("latency_s", 0.0),
        )
    raise NotImplementedError(
        f"provider {config.provider!r} is not registered — see POA/09 §10.1 "
        "(provider/model and hosting region are still open)"
    )
