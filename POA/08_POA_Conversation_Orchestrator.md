# POA — Conversation Orchestrator (Context + Personalisation)

**Module ID:** M08 | **Flow stage:** 3 | **Flow nodes:** Q, T, U, V
**Status:** **Implementation landed 2026-09-01** (Shagun, Track B) — see §11
**Depends on:** M03, M09 (LLM), M10 (verification), M11 (delivery) | **Triggered by:** M05/M06 (fire)

---

## 11. Implementation status (2026-09-01)

Code: `services/conversation/orchestrator/` — `context.py`, `personalisation.py`, `prompts.py`,
`state.py`, `service.py`.
Tests: `tests/test_orchestrator_service.py` (30 tests; 282 in the suite, all green).

**All seven §5 tasks implemented:**

| # | Task | Status |
|---|------|--------|
| 1 | Conversation state schema + store | ✅ `Conversation`/`Turn` + `InMemoryConversationStore` (Postgres needs Prasad's A1) |
| 2 | Context assembler with caching | ✅ `ContextAssembler`, TTL-cached, degrades rather than aborting |
| 3 | Personalisation resolver | ✅ `PersonalisationResolver` — taxonomy derived, see below |
| 4 | Versioned prompt templating + guardrails | ✅ `PromptBuilder`, version `m08-v1`, guardrails asserted by test |
| 5 | Orchestration pipeline | ✅ `on_fire()` — generate → verify → deliver → persist → confirm |
| 6 | Reservation confirm/rollback with M05 | ◐ protocol + in-memory impl; **the real contract is unagreed** — cross-track, added to POA/18 §5 |
| 7 | Latency budget + timeouts | ✅ one `Deadline` the chain draws down against |

### The PII boundary — the claim, stated precisely

§8's fourth risk is "PII sent to LLM". S4 built `reference.redact(text, spans)`, which *applies*
redaction but does not *detect* it, and M08 has no detector. Inventing one here would repeat the
mistake S4 avoided: a detector that quietly under-detects looks like coverage.

So the guarantee is the smaller, checkable one, and it matches POA/15 §4's **field allow-lists**:
the context bundle carries only allow-listed fields, and a test asserts the allow-list is disjoint
from every field `pii.PII_FIELDS` marks as PII. Adding a PII-bearing field to a bundle fails the
suite rather than shipping a customer's name to a provider. A second test walks the whole S4
fixture corpus and asserts none of those values appears in a built prompt.

That covers *generated* context, which is all M08 assembles. `FREE_TEXT_FIELDS` is deliberately
empty **and asserted empty**: free text would need a detector plus `redact()`, and the allow-list
alone cannot save it — so that day is a failing test, not a silent regression.

### Prompt injection — mitigated, not solved

Injected context sits in an explicit fenced block the guardrails describe as data. That raises the
cost of an attack; it does not eliminate it. What actually holds is the **layering**: a successful
injection that persuades the model to quote "£1/day" still has to pass M10, which checks it against
the live booking API and strips or corrects it. There is a test for exactly that. Prompt hardening
is the first line; verification is the one that holds.

### The five failure points

§6 requires that on any downstream failure the customer still receives a safe fallback. Each stage
fails differently and each is tested independently:

| Stage fails | Behaviour |
|---|---|
| Context assembly | bundle marked `degraded`; the prompt tells the model not to cite past bookings |
| Latency budget | fallback, never a partial send |
| M09 | already returns its own templated fallback |
| **M10 returns `blocked`** | fallback — the easy one to miss: `blocked` is a *successful* call that yields no deliverable text, so a naive orchestrator sends nothing |
| M11 delivery | conversation marked failed, and the M05 reservation is **rolled back** |

Rollback matters more than it looks: a fired trigger consumes one of the customer's capped
engagements, and not handing it back means the customer silently loses an engagement they never
received.

### Two bugs caught while building this

- **`json.dumps` was escaping non-ASCII**, so `£` and accented characters became backslash-u
  escapes in the prompt. Beyond being harder for the model to read, it meant a value in the bundle
  no longer appeared verbatim in the prompt — which silently voids any assertion about what did or
  did not reach the provider. Now `ensure_ascii=False`.
- **M09's off-scope heuristic was a false positive on price quotes.** "It's £52.21/day." contains
  none of the on-scope keywords, so a perfectly good reply was being replaced by a generic
  fallback. A message quoting a price is on-scope by definition here — the only thing this
  assistant quotes prices about is rental. Fixed in `LLMService.assess`; the claim still goes
  through M10.

**Open questions — current state:**

- **§10.1 (which HFB service supplies profile + booking history)** — open. `ProfileAdapter` is a
  protocol; `DatasetProfileAdapter` reads the generated dataset today.
- **§10.2 (personalisation taxonomy)** — **derived, not confirmed.** Tone/locale/formality are
  built from what exists in the codebase (`CustomerType`, `Segment`, `Customer.region`, M09's four
  locales). Product should correct it; nothing here is a guess dressed up as a decision.
- **§10.3 (prompt-template ownership)** — open, and the same category as POA/09 §10.4. Both the
  prompt guardrails and the fallback copy are **engineer-written and unowned**. Worth raising with
  product as one question rather than two.

---

## 1. Purpose & scope

When an approved trigger fires (Q), assemble everything the bot needs to say the right thing:
combine the trigger with the customer's profile and booking history (T), apply personalisation for
Customer Type / Market-Region / language (U), build the prompt and call the LLM provider (V). It
orchestrates the downstream LLM (M09), claim verification (M10) and delivery (M11), and it owns the
conversation session/state that M12 continues.

**In scope:** context assembly, personalisation, prompt construction, orchestration of
M09→M10→M11, conversation-state creation.
**Out of scope:** the LLM call internals (M09), verification internals (M10), UI transport (M11).

## 2. Functional requirements
- On fire: build a **context bundle** = trigger + customer profile + booking history + recent
  signals (from M03) + trigger's personalisation hints/message template ref.
- **Personalisation (U):** adjust tone/content/language for Customer Type, Market/Region, language
  before generation. Language selection drives both prompt and fallback template.
- **Prompt build (V):** deterministic prompt template + injected context; enforce guardrails
  (scope, no fabricated offers, must-verify-claims instruction).
- Orchestrate: call M09 (generate/fallback) → if response makes price/rate/availability claims,
  route through M10 (verify) → hand final text to M11 (deliver).
- Create and persist **conversation state** (session, turns, context) that M12 uses for multi-turn.
- Confirm/rollback the M05 engagement reservation based on successful delivery.

## 3. Technical design

### 3.1 Context assembly
```
ContextBundle {
  trigger: {id, signal, context}
  customer: {id, type, region, language, segment}
  booking_history: [...recent bookings...]
  recent_signals: [...from M03...]
  personalisation: {tone, locale, ...}
  template_ref: "tmpl_abandon_payment"
}
```
- Profile/booking history fetched from HFB systems (via a profile adapter) + M03 read models.
- Cache profile lookups briefly to keep fire latency low.

### 3.2 Personalisation
- A personalisation resolver maps (customer_type, region, language) → tone/locale/template variant.
- Language: from customer profile / portal locale; fallback templates localised (coordinate M09).

### 3.3 Prompt construction
- Versioned prompt templates (system + context) with strict guardrails: stay on booking-assist
  scope, never invent prices/availability, defer factual claims to verification, be concise.
- Prompt versions are tracked for reproducibility/audit.

### 3.4 Orchestration & state
- Orchestrator is the state owner: creates `conversation` (id, customer, trigger, status, turns).
- Sequence: generate (M09) → detect claims / verify (M10) → deliver (M11) → persist turn →
  confirm M05 reservation. On failure at any step, fall back (M09 templated) or roll back
  reservation.

## 4. Technology & dependencies
- Python service, Postgres (conversation state), profile/booking adapters, M09/M10/M11 clients.

## 5. Task breakdown
1. Conversation state schema + store.
2. Context assembler (profile + booking + M03 signals) with caching.
3. Personalisation resolver (type/region/language).
4. Versioned prompt templating + guardrails.
5. Orchestration pipeline (generate→verify→deliver→persist→confirm).
6. Reservation confirm/rollback contract with M05.
7. Latency budget + timeouts across the chain.

## 6. Acceptance criteria
- A fired trigger produces a delivered, personalised, verified (where needed) message end-to-end.
- Personalisation demonstrably changes language/tone for different customer profiles.
- Conversation state is persisted and retrievable by M12 for multi-turn.
- On any downstream failure the customer still receives a safe fallback (never nothing, never an
  error), and the engagement reservation is handled correctly.

## 7. Testing strategy
- Unit: context assembly, personalisation resolution, prompt rendering.
- Integration: full fire→deliver with M09/M10/M11 stubs, incl. failure injection.
- Snapshot tests on prompt output for representative triggers/profiles.

## 8. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Slow profile/booking lookups blow latency budget | caching, parallel fetch, timeouts + fallback |
| Prompt injection via customer data | sanitise/escape injected context, strict guardrails |
| Over-personalisation / wrong language | resolver tests, safe default locale |
| PII sent to LLM | minimise/redact context to M09 (M15 policy) |

## 9. Effort & sequencing
Phase 2 core. ~3–4 weeks. Central to the conversation path.

## 10. Open questions
1. Source/adapter for customer profile + booking history (which HFB service)?
2. Personalisation dimensions and their taxonomy (customer types, regions, supported languages)?
3. Prompt-template ownership — product/marketing input needed?
