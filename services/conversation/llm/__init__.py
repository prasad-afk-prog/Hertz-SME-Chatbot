"""M09 — LLM Integration & Fallback Service (POA/09).

The resilience gate on generation: an error never reaches the customer.

    from services.conversation.llm import LLMService, LLMConfig, build_provider

See `service.py` for the Y -> W -> X flow and why the confidence threshold
delegates to `generator.reference.decide_llm`.
"""
from .fallback import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    FallbackCatalogue,
    RenderedFallback,
)
from .provider import (
    LLMProvider,
    LLMResult,
    MockProviderAdapter,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    RetryingProvider,
    Usage,
)
from .service import (
    FallbackReason,
    GenerationOutcome,
    GenerationRecord,
    LLMConfig,
    LLMService,
    build_provider,
)

__all__ = [
    "DEFAULT_LOCALE",
    "FallbackCatalogue",
    "FallbackReason",
    "GenerationOutcome",
    "GenerationRecord",
    "LLMConfig",
    "LLMProvider",
    "LLMResult",
    "LLMService",
    "MockProviderAdapter",
    "ProviderError",
    "ProviderTimeout",
    "ProviderUnavailable",
    "RenderedFallback",
    "RetryingProvider",
    "SUPPORTED_LOCALES",
    "Usage",
    "build_provider",
]
