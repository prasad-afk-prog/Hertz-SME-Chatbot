# POA — Customer Response & Multi-turn Conversation Manager

**Module ID:** M12 | **Flow stage:** 4 | **Flow nodes:** AE–AK
**Status:** **Implementation landed 2026-09-02** (Shagun, Track B) — see §11
**Depends on:** M08 (state), M11 (I/O), M04/M07 (handoff), M09/M10 (turns) | **Feeds:** M14, M07

---

## 11. Implementation status (2026-09-02)

Code: `services/conversation/response/` — `service.py`, `resolution.py`, `handoff.py`.
Tests: `tests/test_response_manager.py` (31 tests; 437 in the suite, all green).

| # | Task | Status |
|---|------|--------|
| 1 | Conversation state machine + persistence | ✅ extends M08's `ConversationStatus`, additively |
| 2 | No-response timeout (AF) + engagement logging | ✅ injected clock; Celery drives it in prod (A7/M15) |
| 3 | Multi-turn loop (M09→M10→M11) + max-turns guardrail | ✅ reuses all three, no second orchestration |
| 4 | Resolution / stuck detector (AH) | ✅ conservative by construction |
| 5 | Deep link back (AI) + booking attribution (AJ) | ◐ logic done; **no real producer for the booking signal** — see below |
| 6 | Handoff-event raiser (AK) → M04/M07 | ◐ **contract proposed, unagreed** — POA/18 §5b item 6 |
| 7 | Terminal-outcome logging → M14 | ✅ `ConversationOutcome` per conversation |

### FINDING — `reference.terminal_state` cannot express this flow

`TerminalState` has three members — `no_engagement`, `converted`, `handed_off` — but §3.1's diagram
has **four** outcomes. A customer the bot helped, who was shown a deep link and has not (yet)
booked, is none of the three.

Calling `terminal_state(responded=True, resolves=True)` at the resolution point returns
`converted`, which **counts a booking that never happened** — inflating the exact conversion metric
M14 reports and this feature is judged on. A test caught it.

M12 therefore leaves `ConversationOutcome.terminal` as `None` between AI and AJ, and only sets
`converted` once a real booking is attributed. **The gap is surfaced rather than forked**: the
shared spec in `generator/reference.py` is the right place to fix it, once its owner decides what
the fourth outcome is called (`resolved_not_booked`?). Until then M12 does not lie about it.

### Decisions worth carrying forward

- **Hitting max-turns raises a handoff; it never silently closes.** §3.1 asks for escalation on
  repeated failure, and a customer dropped mid-conversation is worse than one handed to a person.
- **The handoff routes through M04, not straight to M07** (§3.3). M04 is the single place that
  decides what happens to a signal; bypassing it would put a second routing brain in the system.
  Prasad's `triggers/evaluator.py` already documents the receiving seam.
- **A handoff is raised, never handled inline** (§2). The event carries no queue, agent or skill —
  M12 states what happened and hands over the whole transcript. A handoff that makes the customer
  repeat themselves is the failure this feature exists to prevent.
- **`resolved` has to be earned.** The detector defaults to `unresolved`; only explicit customer
  confirmation reaches `resolved`. Erring toward a person costs an agent's time; erring the other
  way strands someone who still needs help.
- **Complaints and billing disputes are never bot-resolvable**, whatever the customer says. The S3
  trees (CV-12, CV-13) pinned that expectation; here it becomes behaviour.
- **An unverifiable claim escalates** rather than being papered over — if we could not state a fact
  safely, we do not pretend we helped.
- **A booking before the conversation is never attributed**, and the window boundary is inclusive
  and asserted at both edges. Attribution errors flow straight into the conversion metric.

**Open questions — current state:**

- **§10.x / task 5:** AJ's booking-completion signal comes from the M01/M02 stream. A2 is merged so
  events can be consumed, but **M01 (A3) does not exist**, so nothing produces a real
  booking-completion signal yet. `BookingSignal` is the shape M12 expects; the attribution logic is
  tested, the end-to-end path is not.
- **Task 6:** the `HandoffEvent` shape is a **proposal**. It deliberately mirrors Prasad's
  `FireSink` (Pydantic message + Protocol + in-memory impl) so wiring it is recognisable. Needs
  agreeing — POA/18 §5b item 6.

---

## 1. Purpose & scope

Manage what happens after the customer sees the proactive message: did they respond (AE)? If not,
log "no engagement" (AF). If yes, run the **multi-turn conversation with full context retained**
(AG), decide whether the bot resolves the query/hesitation (AH); if resolved, **deep link back** to
the prior search/booking step (AI) toward a completed booking (AJ); if not, **raise a handoff
event** (AK) that goes back to the Trigger Evaluation Engine (M04) → Human Handoff Manager (M07).

**In scope:** response detection, multi-turn state machine, per-turn orchestration (reuse
M08→M09→M10→M11), resolution decision, deep-link back, handoff-event raising, outcome logging.
**Out of scope:** the handoff routing itself (M07), attribution reporting (M14).

## 2. Functional requirements
- **Response detection (AE):** determine engagement vs. no-response within a window; no response ⇒
  log AF (feeds engagement-rate metric, M14).
- **Multi-turn (AG):** continue the conversation with retained context across turns; each customer
  turn goes through generate (M09) → verify claims (M10) → deliver (M11), appended to conversation
  state (owned by M08).
- **Resolution decision (AH):** determine whether the bot has actually helped (intent resolved /
  hesitation cleared) vs. is stuck.
- **Deep link back (AI) → booking (AJ):** on resolution, surface a deep link to the exact prior
  step; detect/attribute the subsequent booking completion (with M14).
- **Handoff (AK):** when the bot can't help, raise a structured handoff event (not handle it
  inline) → M04 → M07, with full context preserved.
- Persist every turn and the terminal outcome (no-response / resolved+booked / handoff).

## 3. Technical design

### 3.1 Conversation state machine
```
DELIVERED ─► (timeout, no reply) ─► NO_ENGAGEMENT (AF, log)
DELIVERED ─► (reply) ─► ACTIVE ──(turn loop: M09→M10→M11)── ACTIVE
ACTIVE ─► resolved? yes ─► DEEP_LINK ─► (booking detected) ─► CONVERTED (AJ)
ACTIVE ─► resolved? no/stuck ─► RAISE_HANDOFF (AK) ─► HANDED_OFF
```
- No-response timeout via a scheduled check (Celery) per delivered conversation.
- Resolution signal: combination of intent classification, explicit customer confirmation, and/or
  a bounded max-turns / stuck detector (repeated failures ⇒ escalate).

### 3.2 Turn orchestration
- Reuse M08's orchestration for each bot turn (context retained from conversation state). Enforce a
  per-turn latency budget and max-turns guardrail.

### 3.3 Handoff-event raising (AK)
- Emit a handoff event carrying conversation_id, transcript, triggering signal, and reason. Route
  it to M04 (which recognises the "handoff" event type) → M07. Context preserved end to end.

### 3.4 Conversion attribution (AJ)
- Detect the booking-completion event (from M01/M02 signal stream) for a customer within an
  attribution window of a resolved conversation; mark CONVERTED and notify M14.

## 4. Technology & dependencies
- Conversation state store (M08), Celery (no-response timers), M09/M10/M11 clients, M04 handoff
  path, M14 outcome logging, intent/resolution classifier (LLM or lightweight model).

## 5. Task breakdown
1. Conversation state machine + persistence (with M08).
2. No-response timeout job (AF) + engagement logging.
3. Multi-turn loop wiring (M09→M10→M11 per turn) + max-turns guardrail.
4. Resolution/stuck detector (AH).
5. Deep-link-back action (AI) + booking-completion attribution (AJ).
6. Handoff-event raiser (AK) → M04/M07 with full context.
7. Terminal-outcome logging → M14.

## 6. Acceptance criteria
- No response within window ⇒ conversation closed as no-engagement and counted correctly (M14).
- A responding customer gets coherent multi-turn replies with retained context, each verified where
  needed.
- When the bot resolves, the customer receives a working deep link back to the prior step; a
  subsequent booking is attributed to the intervention.
- When the bot can't help, a handoff event with full context reaches M07 (never handled ad-hoc
  inline).

## 7. Testing strategy
- Unit: state-machine transitions, resolution/stuck detection, timeout logic.
- Integration: scripted multi-turn conversations to each terminal state.
- Attribution test: booking after resolution is linked; unrelated booking is not.

## 8. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Bot loops without resolving | max-turns + stuck detector ⇒ auto-handoff |
| Over/under-attributing conversions | bounded attribution window + clear rules with M14 |
| Context lost across turns | single conversation-state source of truth (M08) |
| Late replies after timeout | grace handling / reopen policy |

## 9. Effort & sequencing
Phase 2 (multi-turn) with a Phase 3 tie-in for handoff (M07). ~3–4 weeks.

## 10. Open questions
1. No-response window length? Reopen policy for late replies?
2. How is "resolved" determined — explicit confirmation, classifier, or heuristic?
3. Conversion attribution window and rules?
4. Max turns before forced handoff?
