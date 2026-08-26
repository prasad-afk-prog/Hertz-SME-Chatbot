# POA — Conversation Orchestrator (Context + Personalisation)

**Module ID:** M08 | **Flow stage:** 3 | **Flow nodes:** Q, T, U, V | **Status:** Draft
**Depends on:** M03, M09 (LLM), M10 (verification), M11 (delivery) | **Triggered by:** M05/M06 (fire)

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
