# POA — LLM Integration & Fallback Service

**Module ID:** M09 | **Flow stage:** 3 | **Flow nodes:** W, X, Y | **Status:** Draft
**Depends on:** M08 (caller) | **Feeds:** M10 (claims to verify) / M08 (draft or fallback)

---

## 1. Purpose & scope

Call the configured LLM provider to generate the draft response (Y), decide whether the AI is
**available and confident** (W), and when it is not (timeout / low confidence / outage) return a
**safe templated contextual fallback** (X). This module is the truthfulness-and-resilience gate on
generation.

**In scope:** provider adapter, timeouts/retries, confidence assessment, fallback template engine,
cost/latency controls, prompt/response logging.
**Out of scope:** prompt content/context (M08 builds it), factual verification (M10).

## 2. Functional requirements
- Provider-agnostic adapter (Anthropic Claude default) — swap provider/model via config.
- **Availability & confidence check (W):**
  - availability: response within timeout, no provider error;
  - confidence: model/self-reported confidence and/or heuristic checks (empty, off-scope, refusal,
    policy flags) → below threshold ⇒ treat as low confidence.
- **Fallback (X):** deterministic, localised, context-aware templated message per trigger/signal
  (never an error to the customer).
- Streaming optional for UI responsiveness (coordinate M11).
- **Guardrails:** enforce output constraints (length, scope, no fabricated offers); strip/flag
  disallowed content.
- Observability: log prompt version, tokens, latency, confidence decision, fallback usage.

## 3. Technical design

### 3.1 Provider adapter
```python
class LLMProvider(Protocol):
    async def generate(self, prompt: Prompt, *, timeout: float) -> LLMResult: ...
# LLMResult: text, finish_reason, confidence?, usage, latency, safety_flags
```
- Concrete `AnthropicProvider` (default). Config: model id, temperature, max tokens, timeout,
  retries. Circuit breaker around the provider.

### 3.2 Confidence assessment (W)
- Combine signals: provider errors/timeouts ⇒ unavailable; refusal/empty/off-topic classifier ⇒
  low confidence; optional secondary self-eval. Thresholds configurable.
- Decision: `use_llm` vs `use_fallback`, with reason recorded.

### 3.3 Fallback template engine (X)
- Per-trigger, per-locale templates with safe variable slots (e.g. "You were looking at rates for
  {route} — want a hand finishing that booking?"). No unverified price/availability claims in
  templates either (or only ones M10 can/should verify).
- Central catalogue, versioned, editable (candidate for admin control via M13 later).

### 3.4 Resilience
- Timeouts, bounded retries with jitter, circuit breaker; on open circuit ⇒ straight to fallback.
- Cost controls: max tokens, request budgets, per-customer/session limits.

## 4. Technology & dependencies
- LLM provider SDK (Anthropic), template engine (Jinja2 or similar, sandboxed), Redis (circuit
  breaker state / rate budgets).

## 5. Task breakdown
1. Provider adapter interface + Anthropic implementation.
2. Timeout/retry/circuit-breaker wrapper.
3. Confidence/availability decision (W) + reason logging.
4. Fallback template catalogue + localisation + renderer.
5. Output guardrails/sanitisation.
6. Cost/latency telemetry + budgets.
7. Provider/model config surface.

## 6. Acceptance criteria
- On provider timeout/error, a localised fallback is returned within the latency budget (no error
  reaches the customer).
- Low-confidence/off-scope generations are caught and replaced by fallback.
- Provider/model is switchable via config with no code change.
- Every generation logs prompt version, decision, tokens, latency.

## 7. Testing strategy
- Unit: confidence decision truth table, template rendering, guardrails.
- Fault injection: provider timeout/500/malformed → fallback path.
- Regression: golden prompts → acceptable outputs; verify fallbacks are on-scope + localised.

## 8. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Provider outage/latency | circuit breaker + fallback + timeouts |
| Hallucinated offers slip through | scope guardrails + mandatory M10 verification for claims |
| Confidence heuristic wrong | conservative thresholds, prefer fallback when unsure |
| Runaway cost | token caps, budgets, per-session limits |

## 9. Effort & sequencing
Phase 2, with M08. ~2–3 weeks.

## 10. Open questions
1. Provider/model + hosting region (data residency for Hertz)?
2. Is a numeric confidence available, or heuristic-only?
3. Streaming to HS-103 (M11) required, or full-message delivery?
4. Who authors/owns fallback copy?
