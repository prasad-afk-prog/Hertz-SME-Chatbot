# POA — Customer Journey & Behavioural Event Capture

**Module ID:** M01 | **Flow stage:** 1 | **Flow nodes:** A–J | **Status:** Draft
**Depends on:** M02 (event contract) | **Consumed by:** M02 → M03 → M04

---

## 1. Purpose & scope

Instrument the **post-login HFB portal and booking widget** so that every behaviour matching a
trackable pattern emits a structured event to the Ingestion API. Because the customer is already
authenticated, every event is tied to a known identity.

**In scope:** client-side (or portal-server-side) detection and emission of the eight signal
types; event schema/contract; batching, retry and offline-buffering; session correlation.
**Out of scope:** deciding whether a signal becomes an engagement (M04/M05) — this module only
*detects and reports*.

## 2. Signals to capture (from flow nodes C–J)

| Node | Signal | Detection basis | Deferred? |
|------|--------|-----------------|-----------|
| C | Search, no convert | search executed, no booking created in session | in-session |
| D | Rate view, no progress | rates/vehicle options viewed, no advance to booking step | in-session |
| E | Booking abandoned | booking flow started, exited mid-flow — **capture the exact step** | in-session |
| F | Error hit | error/validation failure during search or booking | in-session |
| G | Extended dwell | unusually long time on a single step, no progress | in-session |
| H | Session ended, no booking | login → session end with no booking | in-session |
| I | Deferred: repeated search | same route/dates across >1 session, never booked | deferred |
| J | Deferred: dormant customer | no booking for defined dormancy period | deferred |

## 3. Functional requirements

- Emit an event within a bounded latency of the behaviour (target < 1s for in-session signals).
- Every event carries: identity (customer id), session id, signal type, timestamp, and a
  **signal-specific payload** (e.g. abandoned-step, search route/dates, error code, dwell ms).
- **Session correlation:** stitch multiple events into one session; expose a stable `session_id`.
- **Resilience:** buffer events if the Ingestion API is unreachable; retry with backoff; never
  block the customer UX.
- **Privacy:** send identifiers/opaque ids, not raw PII beyond what the backend needs; respect
  consent flags.
- Signals I and J are typically **derived server-side** (they span sessions) — this module defines
  the raw inputs (search fingerprints, last-booking timestamp) that make them computable.

## 4. Technical design

### 4.1 Capture approach
Two candidate placements — **decide with portal team (open question)**:
- **(A) Client-side JS SDK** embedded in the portal/widget — richest behavioural signal (dwell,
  mid-flow exit) but needs offline buffering and is tamper-visible.
- **(B) Server-side emission** from the portal backend on known state transitions — more reliable,
  less granular for dwell/abandonment timing.
- **Recommended:** hybrid — client SDK for interaction/dwell/abandon signals, server-side for
  authoritative booking/search state and for deriving deferred signals I/J.

### 4.2 Event contract (shared with M02 — canonical schema lives with M02)
```jsonc
{
  "event_id": "uuid",              // client-generated, for idempotency
  "customer_id": "hfb-cust-123",
  "session_id": "sess-abc",
  "signal_type": "booking_abandoned",
  "occurred_at": "2026-08-24T10:15:30Z",
  "source": "booking_widget|portal",
  "context": {                     // signal-specific
    "step": "payment",
    "route": {"from": "LHR", "to": "MAN"},
    "dates": {"pickup": "2026-09-01", "return": "2026-09-05"},
    "error_code": null,
    "dwell_ms": 42000
  },
  "consent": {"marketing": true, "analytics": true},
  "sdk_version": "1.0.0"
}
```

### 4.3 Delivery
- Batch small events (debounce ~250–500ms), flush on visibility-change / before-unload.
- Use `sendBeacon` (client) or async HTTP with retry (server) for the "session ended" edge.
- Local buffer (IndexedDB / in-memory queue) with capped size + TTL.

## 5. Technology & dependencies
- Client SDK: lightweight TypeScript/JS (no heavy deps), or the portal's existing analytics layer.
- Depends on the portal team for embed points and on M02 for the accepted contract.

## 6. Task breakdown
1. Finalise capture approach (A/B/hybrid) with portal team.
2. Define & version the event contract jointly with M02.
3. Implement detectors for each of C–H (in-session).
4. Implement offline buffering + retry/backoff + `sendBeacon` flush.
5. Define the raw fields needed for server-derived I/J and where they are stored/read.
6. Consent + PII gating.
7. Instrumentation QA harness (simulate each signal type).

## 7. Acceptance criteria
- Each of the eight signal types can be reproducibly generated in a test portal and appears at the
  Ingestion API with a correct, schema-valid payload.
- Abandonment events include the exact step; error events include the error code.
- No event loss under transient API outage (buffered and replayed).
- No customer-visible latency regression on the portal.

## 8. Testing strategy
- Unit: each detector fires on the right condition and not otherwise.
- Integration: SDK → Ingestion API contract tests (schema validation).
- Resilience: kill the API, verify buffering/replay; verify idempotency via `event_id`.
- Privacy: assert no disallowed PII fields are emitted when consent is off.

## 9. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Client SDK misses events on tab close | `sendBeacon`, before-unload flush, server-side backstop for critical states |
| Double-counting on retries | client `event_id` + idempotent ingestion (M02/M03) |
| Portal team can't embed client JS | fall back to server-side emission |
| PII leakage | consent gating, field allow-list, review with security (M15) |

## 10. Effort & sequencing
Phase 1. Blocks M02 usefulness. ~2–3 weeks with portal-team collaboration; the contract must
be frozen early because M02–M05 depend on it.

## 11. Open questions
1. Client SDK vs server-side vs hybrid — who owns the portal embed?
2. Are I/J derived here or entirely inside M04? (recommend: raw inputs here, derivation in M04.)
3. What consent framework governs behavioural tracking for Hertz customers?
4. Existing analytics layer we can reuse vs. greenfield SDK?
