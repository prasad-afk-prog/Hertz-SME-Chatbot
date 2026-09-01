# POA — LLM Integration & Fallback Service

**Module ID:** M09 | **Flow stage:** 3 | **Flow nodes:** W, X, Y
**Status:** **Phase-1 implementation landed 2026-09-01** (Shagun, Track B) — see §11
**Depends on:** M08 (caller) | **Feeds:** M10 (claims to verify) / M08 (draft or fallback)

---

## 11. Implementation status (2026-09-01)

Code: `services/conversation/llm/` — `provider.py`, `fallback.py`, `service.py`.
Tests: `tests/test_llm_fallback_service.py` (33 tests; 177 in the suite, all green).

**Of the seven §5 tasks:**

| # | Task | Status |
|---|------|--------|
| 1 | Provider adapter interface + Anthropic implementation | ◐ `LLMProvider` protocol + `MockProviderAdapter` done; **Anthropic deferred** pending §10.1 |
| 2 | Timeout / retry / circuit-breaker wrapper | ✅ `RetryingProvider`; **jitter deferred** (see below) |
| 3 | Confidence / availability decision (W) + reason logging | ✅ delegates to `reference.decide_llm`, layered heuristics on top |
| 4 | Fallback catalogue + localisation + renderer | ✅ 8 signals × 4 locales, claim-free by test |
| 5 | Output guardrails / sanitisation | ✅ refusal, off-scope, length, safety flags |
| 6 | Cost / latency telemetry | ◐ `Usage` per generation; **budgets/limits deferred** (need Redis + M15) |
| 7 | Provider / model config surface | ✅ `LLMConfig` + `build_provider()` |

### Recorded deviations

**Sync, not async.** §3.1 shows `async def generate`. The implementation is
synchronous: everything else in this repo is sync, and going async would pull
`pytest-asyncio` into `requirements-dev.txt` — a shared file, with Prasad's S5
load work the other likely claimant (POA/18 §4 says announce before touching
those). The protocol is otherwise shape-for-shape identical, so the migration is
mechanical once a real SDK lands and async is actually needed.

**Retry jitter is not implemented.** §3.4 asks for "bounded retries with
jitter". Bounded retries are in; jitter is deferred because it cannot be
asserted deterministically without injecting a randomness source that the tests
would then have to pin — at which point the test proves nothing about real
jitter. A flaky test in a sub-second suite is worse than a documented gap. Add
it with the real provider, where it actually matters for thundering-herd.

### Design decisions worth carrying forward

- **The W threshold delegates to `reference.decide_llm`**, which
  `test_golden_scenarios.py` already asserts against via GS-06. The refusal,
  off-scope and length heuristics layer *around* that call rather than replacing
  it, so the golden scenario keeps covering what ships. A test pins the agreement.
- **No fallback template may assert a price, rate or availability**, asserted
  over every template in every locale. Fallbacks fire when the pipeline is
  *already* degraded, so a template quoting a figure would walk straight around
  M10. This is the highest-value test in the module.
- **A missing template slot degrades to the generic localised message** rather
  than rendering `options for {route}` at a customer.
- **An unsupported locale is served English *and flagged*** (`locale_missing`),
  so a missing translation surfaces as a gap instead of shipping silently.
- **Fallbacks carry no claims**, so M10 has nothing to verify on that path —
  which is the point.
- **A draft that survives W keeps its claims intact for M10.** §8's mitigation
  for hallucinated offers is mandatory downstream verification, not cleverness
  here.
- **`CircuitBreaker`/`TTLCache` moved to `services/common/resilience.py`** when
  M09 needed the same breaker as M10. Two implementations would drift, and one
  would eventually be the wrong one.

**Still open:** §10.1 (provider/model + hosting region for Hertz data residency)
blocks the real adapter. §10.2 (is numeric confidence available?) — the code
assumes it may be `None` and falls back conservatively, but the heuristics would
be tuned differently given a real signal. §10.3 (streaming) is unaddressed;
§10.4 (who owns fallback copy) matters before this copy goes near production —
it is engineer-written placeholder text.

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
