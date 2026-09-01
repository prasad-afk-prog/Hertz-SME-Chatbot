"""M09 LLM Integration & Fallback — POA/09 acceptance criteria (§6) and the
fault-injection tests §7 asks for.

The guarantee: **an error never reaches the customer.** Provider down, too slow,
empty, refusing, off-scope or unconfident — every path ends in a safe, localised,
claim-free message.

The most important test in this file is
`test_no_fallback_template_asserts_a_price_or_availability`. Fallbacks fire when
the pipeline is *already* degraded, so a template quoting a figure would walk
straight around M10's verification.
"""
from __future__ import annotations

import re

import pytest

from generator.models import LLMResponse, MessageKind, SignalType
from mocks.llm_provider import LLMProviderMock
from services.conversation.claim_verification.detection import mentions_money
from services.conversation.llm import (
    SUPPORTED_LOCALES,
    FallbackCatalogue,
    FallbackReason,
    LLMConfig,
    LLMService,
    MockProviderAdapter,
    ProviderTimeout,
    RetryingProvider,
    build_provider,
)
from services.conversation.llm.provider import ProviderUnavailable


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def svc(response=None, *, timeout_mode=False, latency=0.0, config=None, **kw):
    mock = LLMProviderMock(response, timeout=timeout_mode)
    provider = MockProviderAdapter(mock, latency_s=latency, **kw)
    return LLMService(provider, config or LLMConfig())


def good(text="Happy to help you finish your booking — shall I hold that vehicle?", conf=0.9):
    return LLMResponse(text=text, confidence=conf)


# --- §6: an error never reaches the customer --------------------------------- #
def test_provider_outage_returns_a_fallback_not_an_error():
    out = svc(None, timeout_mode=True).generate("p", signal=SignalType.booking_abandoned)
    assert out.message_kind is MessageKind.fallback
    assert out.text and "error" not in out.text.lower()
    assert out.record.reason is FallbackReason.provider_unavailable
    assert out.response is None


def test_provider_timeout_returns_a_fallback():
    out = svc(good(), latency=9.0, config=LLMConfig(timeout_s=1.0)).generate(
        "p", signal=SignalType.error_hit
    )
    assert out.message_kind is MessageKind.fallback
    assert out.record.reason is FallbackReason.provider_timeout


def test_healthy_confident_generation_is_used():
    out = svc(good()).generate("p", signal=SignalType.search_no_convert)
    assert out.message_kind is MessageKind.llm
    assert out.record.decision == "use_llm"
    assert out.record.reason is FallbackReason.none


# --- W: the confidence / availability truth table (§7 unit) ------------------ #
@pytest.mark.parametrize(
    "response, expected",
    [
        (None, FallbackReason.provider_unavailable),
        (LLMResponse(text="   ", confidence=0.9), FallbackReason.empty_response),
        (LLMResponse(text="Shall I help with your booking?", confidence=0.1),
         FallbackReason.low_confidence),
        (LLMResponse(text="As an AI, I can't help with that booking.", confidence=0.9),
         FallbackReason.refusal),
        (LLMResponse(text="The weather in Paris is lovely today.", confidence=0.9),
         FallbackReason.off_scope),
        (LLMResponse(text="booking " * 500, confidence=0.9), FallbackReason.too_long),
        (LLMResponse(text="Shall I help with your booking?", confidence=0.9),
         FallbackReason.none),
    ],
)
def test_confidence_decision_truth_table(response, expected):
    assert svc().assess(response) is expected


def test_safety_flags_force_a_fallback():
    assert svc().assess(good(), safety_flags=["self_harm"]) is FallbackReason.safety_flagged


def test_low_confidence_generation_is_replaced_by_fallback():
    out = svc(LLMResponse(text="Maybe try booking again?", confidence=0.05)).generate(
        "p", signal=SignalType.rate_view_no_progress
    )
    assert out.message_kind is MessageKind.fallback
    assert out.record.reason is FallbackReason.low_confidence


def test_threshold_comes_from_config():
    response = LLMResponse(text="Shall I help with your booking?", confidence=0.4)
    assert svc(config=LLMConfig(confidence_threshold=0.9)).assess(response) is FallbackReason.low_confidence
    assert svc(config=LLMConfig(confidence_threshold=0.2)).assess(response) is FallbackReason.none


def test_service_agrees_with_the_reference_decision():
    """W delegates to reference.decide_llm, which GS-06 already asserts against.
    This pins that the delegation is real and not a divergent copy."""
    from generator.reference import decide_llm

    for response in (None, LLMResponse(text="", confidence=0.9),
                     LLMResponse(text="booking help", confidence=0.1),
                     LLMResponse(text="booking help", confidence=0.9)):
        reference_says = decide_llm(response, threshold=0.5)
        service_says = svc().assess(response)
        if reference_says == "fallback":
            assert service_says is not FallbackReason.none
        else:
            assert service_says is FallbackReason.none


# --- X: the fallback catalogue ----------------------------------------------- #
_AVAILABILITY_WORDS = re.compile(
    r"\b(?:in stock|sold out|units? (?:left|remaining)|seats? left)\b", re.IGNORECASE
)
_MONEY_WORDS = re.compile(
    r"\b(?:pounds?|euros?|dollars?|free of charge|no charge|discount|% off)\b", re.IGNORECASE
)


def test_no_fallback_template_asserts_a_price_or_availability():
    """THE test for this module. Fallbacks fire when the pipeline is already
    degraded, so a template quoting a figure would bypass M10 entirely."""
    for signal, locale, text in FallbackCatalogue().all_strings():
        where = f"{signal.value}/{locale}"
        assert not mentions_money(text), f"{where}: currency symbol in a fallback template"
        assert not _MONEY_WORDS.search(text), f"{where}: money wording in a fallback template"
        assert not _AVAILABILITY_WORDS.search(text), f"{where}: availability claim in a template"
        assert not re.search(r"\d+(?:[.,]\d{2})?\s*(?:/|per )?day", text), f"{where}: a rate"


def test_symbol_free_money_wording_is_covered_by_a_separate_check():
    """`mentions_money` only matches £/€ — pinning that gap, and that the
    template corpus has its own word-level check to compensate."""
    assert not mentions_money("that will be fifty pounds")
    assert _MONEY_WORDS.search("that will be fifty pounds")


def test_every_signal_has_every_supported_locale():
    catalogue = FallbackCatalogue()
    for signal in SignalType:
        by_locale = catalogue.templates.get(signal)
        assert by_locale, f"{signal.value} has no fallback template"
        assert set(by_locale) == set(SUPPORTED_LOCALES), f"{signal.value} is missing a locale"


def test_templates_are_localised_not_copies():
    for signal, by_locale in FallbackCatalogue().templates.items():
        assert len(set(by_locale.values())) == len(by_locale), \
            f"{signal.value}: two locales share identical copy — likely an untranslated paste"


def test_context_slots_are_filled():
    out = FallbackCatalogue().render(
        SignalType.search_no_convert, "en", {"route": "London Heathrow"}
    )
    assert "London Heathrow" in out.text
    assert "{" not in out.text
    assert not out.used_generic


def test_missing_slot_degrades_to_generic_rather_than_leaking_a_placeholder():
    """A customer must never see 'options for {route}'."""
    out = FallbackCatalogue().render(SignalType.search_no_convert, "en", {})
    assert out.used_generic
    assert "{" not in out.text and "route" not in out.text


def test_fallback_is_localised():
    generic_en = FallbackCatalogue().render(None, "en", {}).text
    for locale in ("de", "fr", "es"):
        out = FallbackCatalogue().render(SignalType.dormant, locale, {})
        assert out.locale == locale
        assert out.text != generic_en


def test_unknown_locale_falls_back_to_english_and_is_reported():
    """Silent English for an unsupported locale is a gap, so it is flagged in
    the record rather than passing unnoticed."""
    out = FallbackCatalogue().render(SignalType.dormant, "ja", {})
    assert out.locale == "en"
    assert out.locale_missing
    assert out.text


def test_unknown_locale_is_recorded_on_the_generation_record():
    out = svc(None, timeout_mode=True).generate("p", signal=SignalType.dormant, locale="ja")
    assert out.record.locale_missing
    assert out.record.locale == "en"


def test_fallback_carries_no_claims_for_m10():
    out = svc(None, timeout_mode=True).generate("p", signal=SignalType.booking_abandoned)
    assert out.claims == [], "a fallback must assert nothing verifiable"


# --- §3.4: retries and circuit breaker --------------------------------------- #
def test_retries_are_bounded_and_counted():
    """Assert the count and the bound, never wall-clock timing."""
    slept: list[float] = []
    provider = RetryingProvider(
        MockProviderAdapter(LLMProviderMock(good()), latency_s=99.0),
        max_retries=2, clock=FakeClock(), sleep=slept.append,
    )
    with pytest.raises(ProviderTimeout):
        provider.generate("p", timeout=1.0)
    assert provider.attempts_made == 3            # 1 try + 2 retries
    assert len(slept) == 2
    assert all(s <= 0.25 * 3 for s in slept), "backoff exceeded its bound"


def test_a_successful_retry_returns_the_draft():
    calls = {"n": 0}

    def flaky_latency():
        calls["n"] += 1
        return 99.0 if calls["n"] == 1 else 0.0

    provider = RetryingProvider(
        MockProviderAdapter(LLMProviderMock(good()), latency_s=flaky_latency),
        max_retries=2, clock=FakeClock(), sleep=lambda _s: None,
    )
    result = provider.generate("p", timeout=1.0)
    assert result.attempts == 2


def test_open_circuit_goes_straight_to_fallback_without_calling_the_provider():
    clock = FakeClock()
    inner = MockProviderAdapter(LLMProviderMock(None, timeout=True))
    provider = RetryingProvider(
        inner, max_retries=0, breaker_threshold=2, clock=clock, sleep=lambda _s: None
    )
    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            provider.generate("p", timeout=1.0)
    assert provider.breaker.state == "open"

    service = LLMService(provider, LLMConfig())
    out = service.generate("p", signal=SignalType.dormant)
    assert out.message_kind is MessageKind.fallback
    assert out.record.reason is FallbackReason.provider_unavailable


def test_breaker_recovers_after_cooldown_on_the_injected_clock():
    clock = FakeClock()
    inner = MockProviderAdapter(LLMProviderMock(good()))
    provider = RetryingProvider(
        inner, breaker_threshold=1, breaker_cooldown_s=30.0, clock=clock, sleep=lambda _s: None
    )
    provider.breaker.record_failure()
    assert provider.breaker.state == "open"
    clock.advance(31.0)
    assert provider.generate("p", timeout=1.0).response.text
    assert provider.breaker.state == "closed"


# --- §6: provider/model switchable by config, no code change ----------------- #
def test_provider_is_selected_from_config_alone():
    a = build_provider(LLMConfig(provider="mock", model_id="model-a"))
    b = build_provider(LLMConfig(provider="mock", model_id="model-b"))
    assert a.model_id == "model-a" and b.model_id == "model-b"
    assert LLMService(a).provider.model_id != LLMService(b).provider.model_id


def test_unregistered_provider_fails_loudly_pointing_at_the_open_question():
    with pytest.raises(NotImplementedError, match="10.1"):
        build_provider(LLMConfig(provider="anthropic"))


# --- §6: every generation is logged ------------------------------------------ #
def test_every_generation_logs_prompt_version_decision_tokens_and_latency():
    out = svc(good(), latency=0.42).generate("p", signal=SignalType.search_no_convert)
    r = out.record
    assert r.prompt_version == "v1"
    assert r.model_id == "mock-model-v1"
    assert r.decision == "use_llm"
    assert r.usage.total_tokens > 0
    assert r.usage.latency_s == 0.42
    assert r.confidence == 0.9


def test_fallback_generations_are_logged_with_their_reason():
    out = svc(LLMResponse(text="The weather is nice.", confidence=0.9)).generate(
        "p", signal=SignalType.extended_dwell, context={"vehicle": "an Intermediate"}
    )
    assert out.record.used_fallback
    assert out.record.reason is FallbackReason.off_scope
    assert "Intermediate" in out.text


def test_draft_that_survives_w_still_carries_its_claims_to_m10():
    """M09 does not verify — POA/09 §8's mitigation for hallucinated offers is
    mandatory downstream verification, so claims must reach M10 intact."""
    from decimal import Decimal

    from generator.models import BookingClaim, ClaimKind
    from datetime import datetime, timedelta, timezone

    when = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    claim = BookingClaim(
        kind=ClaimKind.price, pickup="LHR", dropoff="LHR",
        pickup_at=when, return_at=when + timedelta(days=3),
        vehicle_class="ICAR", quoted_price=Decimal("48.50"), text_token="£48.50",
    )
    response = LLMResponse(
        text="Your booking is £48.50/day — shall I hold it?", claims=[claim], confidence=0.9
    )
    out = svc(response).generate("p", signal=SignalType.booking_abandoned)
    assert out.message_kind is MessageKind.llm
    assert out.claims == [claim]
