"""Anthropic provider (M09 §5.1) — the real adapter behind `LLMProvider`.

Closes POA/09 §5 task 1. Sits behind the same protocol as `MockProviderAdapter`,
so `RetryingProvider`, the confidence gate and the fallback catalogue are
unchanged by switching to it.

Three design points worth knowing:

**Claims come back as structured output, not parsed out of prose.** POA/10 §3.1
recommends M09 tag factual claims so M10's detection is exact rather than
regex-guessed. `output_config.format` constrains the response to a JSON schema
carrying both the prose and the claims, so `TaggedClaimDetector` — M10's primary
path — always has real input. This is the M09/M10 contract in code.

**Confidence is self-reported, because the API does not return one.** POA/09
§10.2 asks whether a numeric confidence is available: it is not. The schema asks
the model for one, and `LLMConfig.confidence_threshold` compares against it. A
self-report is weaker evidence than a logprob, so the gate stays conservative
and every other heuristic (refusal, off-scope, length) still applies.

**Latency is the binding constraint.** Generation is inline before delivery, so
this runs at `effort: "low"` by default with a small `max_tokens`: the output is
one short contextual nudge, not an essay. Thinking stays on (adaptive is the
default on Opus 5, and explicitly disabling it on that model has known failure
modes) — low effort is the right lever, not disabled thinking.

The system prompt is cached (`cache_control`), since it is identical across
every request and only the per-customer context varies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from generator.models import BookingClaim, ClaimKind, LLMResponse

from .provider import (
    LLMResult,
    ProviderTimeout,
    ProviderUnavailable,
    Usage,
)

DEFAULT_MODEL = "claude-opus-5"

# The M09 -> M10 contract: prose plus exactly-addressed factual claims.
CLAIM_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "confidence", "claims"],
        "properties": {
            "text": {
                "type": "string",
                "description": "The message to show the customer. One or two sentences.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "How confident you are that this reply is correct and in scope.",
            },
            "claims": {
                "type": "array",
                "description": (
                    "Every factual assertion about price, rate or availability made in `text`. "
                    "Leave empty if the message asserts no such fact."
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "text_token"],
                    "properties": {
                        "kind": {"type": "string", "enum": ["price", "rate", "availability"]},
                        "text_token": {
                            "type": "string",
                            "description": (
                                "The EXACT substring of `text` expressing this claim, "
                                "e.g. '£42.21'. Must appear verbatim in `text`."
                            ),
                        },
                        "quoted_price": {"type": ["string", "null"]},
                        "quoted_available": {"type": ["boolean", "null"]},
                    },
                },
            },
        },
    },
}

SYSTEM_PROMPT = (
    "You are the Hertz for Business booking assistant. You help signed-in business "
    "customers finish or amend a vehicle rental.\n\n"
    "Rules:\n"
    "- Keep replies to one or two short sentences.\n"
    "- Never invent a price, rate or availability. If you state one, it MUST come from "
    "the context given to you, and you MUST list it in `claims` with the exact substring "
    "you used, so it can be verified before the customer sees it.\n"
    "- If you cannot help with something, say so plainly rather than guessing.\n"
    "- Never ask for card details, licence numbers or other personal data in chat.\n"
    "- Stay on the subject of vehicle rental."
)


@dataclass
class AnthropicConfig:
    """Provider-specific settings. The model/provider swap itself is `LLMConfig`."""
    model: str = DEFAULT_MODEL
    max_tokens: int = 1024          # deliberately short: one contextual nudge
    effort: str = "low"             # latency-critical; inline before delivery
    system_prompt: str = SYSTEM_PROMPT
    cache_system_prompt: bool = True
    # POA/09 §10.1 is still open; when Hertz's data-residency answer lands, pin it here.
    inference_geo: str | None = None
    # Opus 5 may decline on safety grounds. Our own fallback (X) already covers
    # that path safely, so server-side re-run is opt-in rather than on.
    server_side_fallback_model: str | None = None


class AnthropicProvider:
    """Concrete `LLMProvider` over the Anthropic Messages API."""

    def __init__(
        self,
        client: Any | None = None,
        config: AnthropicConfig | None = None,
    ) -> None:
        self.config = config or AnthropicConfig()
        self._client = client          # injected in tests; constructed lazily otherwise

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic          # lazy: the package works without the SDK installed

            self._client = anthropic.Anthropic()
        return self._client

    @property
    def model_id(self) -> str:
        return self.config.model

    # ---- request ------------------------------------------------------- #
    def _build_request(self, prompt: str, timeout: float) -> dict[str, Any]:
        system: Any = self.config.system_prompt
        if self.config.cache_system_prompt:
            system = [{
                "type": "text",
                "text": self.config.system_prompt,
                "cache_control": {"type": "ephemeral"},
            }]

        request: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": self.config.effort, "format": CLAIM_SCHEMA},
        }
        if self.config.inference_geo:
            request["inference_geo"] = self.config.inference_geo
        if self.config.server_side_fallback_model:
            request["betas"] = ["server-side-fallback-2026-06-01"]
            request["fallbacks"] = [{"model": self.config.server_side_fallback_model}]
        return request

    def generate(self, prompt: str, *, timeout: float) -> LLMResult:
        import anthropic

        request = self._build_request(prompt, timeout)
        try:
            response = self.client.with_options(timeout=timeout).messages.create(**request)
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeout(str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderUnavailable(f"connection error: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderUnavailable(f"rate limited: {exc}") from exc
        except anthropic.APIStatusError as exc:
            # 4xx is our bug, 5xx is theirs — both end in a safe fallback, but the
            # message is kept so the cause is visible in the log.
            raise ProviderUnavailable(f"status {exc.status_code}: {exc}") from exc

        return self._to_result(response)

    # ---- response ------------------------------------------------------ #
    def _to_result(self, response: Any) -> LLMResult:
        safety_flags: list[str] = []
        # A refusal is a 200 with stop_reason "refusal" — never an exception. The
        # confidence gate turns this into a templated fallback (X).
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            safety_flags.append(f"refusal:{getattr(details, 'category', None) or 'unknown'}")

        text_blocks = [
            block.text for block in getattr(response, "content", []) or []
            if getattr(block, "type", None) == "text"
        ]
        payload = parse_structured_output("".join(text_blocks))
        llm_response = build_llm_response(payload, finish_reason=getattr(response, "stop_reason", "end_turn"))

        usage = getattr(response, "usage", None)
        return LLMResult(
            response=llm_response,
            usage=Usage(
                prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
                completion_tokens=getattr(usage, "output_tokens", 0) or 0,
            ),
            safety_flags=safety_flags,
        )


# --------------------------------------------------------------------------- #
# Structured-output parsing
# --------------------------------------------------------------------------- #
def parse_structured_output(raw: str) -> dict[str, Any]:
    """Parse the constrained JSON response.

    Malformed output is not an exception path: it degrades to an empty draft,
    which the confidence gate (W) turns into a safe fallback. Raising here would
    surface an error to a customer, which POA/09 §1 forbids.
    """
    import json

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return {"text": "", "confidence": 0.0, "claims": []}
    if not isinstance(payload, dict):
        return {"text": "", "confidence": 0.0, "claims": []}
    return payload


def build_llm_response(
    payload: dict[str, Any],
    *,
    finish_reason: str = "end_turn",
    pickup: str | None = None,
    dropoff: str | None = None,
    pickup_at: datetime | None = None,
    return_at: datetime | None = None,
    vehicle_class: str | None = None,
) -> LLMResponse:
    """Turn the structured payload into the contract `LLMResponse`.

    A claim whose `text_token` is not actually in `text`, or that lacks the route
    context needed for a booking-API lookup, is DROPPED rather than passed on —
    M10 cannot verify it, and an unverifiable claim tag is worse than none
    because it looks like coverage. Dropping it leaves the token untagged, and
    M10's pattern fallback still gets a chance at it.
    """
    text = str(payload.get("text") or "")
    confidence = payload.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None

    claims: list[BookingClaim] = []
    have_context = all(v is not None for v in (pickup, dropoff, pickup_at, return_at, vehicle_class))
    for raw_claim in payload.get("claims") or []:
        if not isinstance(raw_claim, dict):
            continue
        token = str(raw_claim.get("text_token") or "")
        if not token or token not in text or not have_context:
            continue
        kind_value = raw_claim.get("kind")
        if kind_value not in {k.value for k in ClaimKind}:
            continue

        quoted_price = None
        if raw_claim.get("quoted_price") is not None:
            try:
                quoted_price = Decimal(str(raw_claim["quoted_price"]))
            except (InvalidOperation, ValueError):
                continue

        claims.append(
            BookingClaim(
                kind=ClaimKind(kind_value),
                pickup=pickup, dropoff=dropoff,
                pickup_at=pickup_at, return_at=return_at,
                vehicle_class=vehicle_class,
                quoted_price=quoted_price,
                quoted_available=raw_claim.get("quoted_available"),
                text_token=token,
            )
        )

    return LLMResponse(
        text=text, claims=claims, confidence=confidence, finish_reason=finish_reason or "end_turn"
    )
