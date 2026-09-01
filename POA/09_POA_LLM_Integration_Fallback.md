# POA — LLM Integration & Fallback Service

**Module ID:** M09 | **Flow stage:** 3 | **Flow nodes:** W, X, Y
**Status:** **Phase-1 implementation landed 2026-09-01** (Shagun, Track B) — see §11
**Depends on:** M08 (caller) | **Feeds:** M10 (claims to verify) / M08 (draft or fallback)

---

## 11. Implementation status (2026-09-01)

Code: `services/conversation/llm/` — `provider.py`, `fallback.py`, `service.py`.
Tests: `tests/test_llm_fallback_service.py` (33 tests; 177 in the suite, all green).

**All seven §5 tasks are now implemented (2026-09-01):**

| # | Task | Status |
|---|------|--------|
| 1 | Provider adapter interface + Anthropic implementation | ✅ `AnthropicProvider` on `claude-opus-5` |
| 2 | Timeout / retry / circuit-breaker wrapper | ✅ `RetryingProvider` (jitter deferred — see below) |
| 3 | Confidence / availability decision (W) + reason logging | ✅ delegates to `reference.decide_llm` |
| 4 | Fallback catalogue + localisation + renderer | ✅ 8 signals × 4 locales, claim-free by test |
| 5 | Output guardrails / sanitisation | ✅ refusal, off-scope, length, safety flags |
| 6 | Cost / latency telemetry + budgets | ✅ `BudgetGuard` — session / customer-daily / global spend |
| 7 | Provider / model config surface | ✅ `LLMConfig` + `build_provider()` |

### The Anthropic adapter (task 1)

`services/conversation/llm/anthropic_provider.py`, on **`claude-opus-5`**.

- **Claims arrive as structured output, not parsed from prose.** `output_config.format`
  constrains the response to a schema carrying the message *and* its factual claims, so M10's
  primary `TaggedClaimDetector` path always has exact input. This is the M09/M10 contract from
  POA/10 §3.1 realised in code, and it removes the reason M10's regex fallback existed.
- **§10.2 answered: there is no native numeric confidence.** The schema asks the model to
  self-report one. A self-report is weaker than a logprob, so the gate stays conservative and every
  other heuristic still applies.
- **Latency is the binding constraint** — generation is inline before delivery, so it runs at
  `effort: "low"` with a small `max_tokens`. Thinking is left adaptive (the default on Opus 5);
  explicitly disabling it on that model has documented failure modes, and low effort is the correct
  lever.
- A claim whose `text_token` is not in the text, or that lacks route context, is **dropped**. An
  unverifiable tag is worse than none — it looks like coverage while M10 has nothing to address.
- Malformed model output degrades to an empty draft (→ fallback) rather than raising: §1 says an
  error never reaches the customer.
- The system prompt is cached, and forbids inventing prices or soliciting personal data in chat.

### Budgets (task 6)

`BudgetGuard` enforces three limits that fail differently: per-session tokens (a runaway
conversation), per-customer daily tokens (one account), and global daily **spend** in money.
Exceeding one is not an error to the customer — it returns the same safe templated fallback as an
outage. Prices are config, not constants: a stale hard-coded rate silently under-reports spend. The
store is a protocol; Redis (§4) slots in behind three methods.

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

**Open questions — current state:**

- **§10.1 (provider/model + hosting region).** Implemented on `claude-opus-5`. `inference_geo` is
  wired and unset: pin it the moment Hertz's data-residency answer lands. That is a config line,
  not a code change.
- **§10.2 (numeric confidence?)** — **answered: no.** The API returns no confidence score, so the
  structured schema asks the model to self-report one. Documented above.
- **§10.3 (streaming to HS-103)** — still open, and now M11's question as much as M09's. Not
  implemented: these messages are one or two sentences, so streaming buys little and adds a
  partial-delivery failure mode. Revisit if HS-103 wants it.
- **§10.4 (who owns fallback copy)** — still open and now the most pressing. The catalogue is
  **engineer-written placeholder text in four languages**; it is claim-free and safe, but it has
  not been through marketing or a native speaker. It should not go in front of customers as-is.

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
