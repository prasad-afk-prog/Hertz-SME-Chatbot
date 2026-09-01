# POA — Chatbot UI Integration (HS-103) & Delivery

**Module ID:** M11 | **Flow stage:** 3 | **Flow nodes:** AD
**Status:** **Implementation landed 2026-09-01** (Shagun, Track B) — see §11
**Depends on:** existing HS-103 chat interface | **Called by:** M08 | **Feeds:** M12 (customer replies)

---

## 11. Implementation status (2026-09-01)

Code: `services/conversation/delivery/service.py`.
Tests: `tests/test_delivery_service.py` (23 tests; 252 in the suite, all green).

**All seven §5 tasks are implemented, one behind an assumption:**

| # | Task | Status |
|---|------|--------|
| 1 | Confirm HS-103 surface + auth with the owning team | ◐ **not ours to close** — §10.1 is a conversation, not code. The adapter is a protocol so the answer changes one class |
| 2 | Delivery adapter (proactive push + actions/deep links) | ✅ `DeliveryService.deliver()` |
| 3 | Inbound reply webhook → M12 | ✅ `on_customer_message()`, idempotent |
| 4 | conversation ↔ thread id mapping store | ✅ `CorrelationStore`, both directions |
| 5 | Delivery / read receipts → M14 | ✅ `DeliveryReceipt` + `engagement` |
| 6 | UX rules for proactive injection (presence, anti-nag) | ✅ presence gate + per-conversation hold |
| 7 | Localisation + rich elements | ✅ text is pre-localised by M09; actions degrade gracefully |

### Design decisions worth carrying forward

- **The transport is a protocol because §10.1 is open.** §8's mitigation for "HS-103 capabilities
  unknown" is adapter abstraction, so everything this module actually decides — presence, anti-nag,
  correlation, receipts, retry — sits *above* the transport and does not move when the real surface
  lands.
- **Anti-nag here is narrower than M05's frequency cap, deliberately.** M05 governs how often a
  customer may be *engaged*; re-implementing that here would double-count and silently halve the
  configured cap. This module only prevents stacking a second proactive message on a conversation
  whose first is still unacknowledged — and it does not apply to replies inside a live exchange,
  which would otherwise break the M12 loop.
- **A delivery ref identifies a message; a thread identifies a conversation.** Conflating them was
  a real bug caught by test — the second message to a conversation tried to rebind it to a new
  thread, which would have misrouted every subsequent reply. An existing binding now wins.
- **Inbound handling is idempotent and never raises.** A webhook that delivers twice is normal, and
  a 500 back to HS-103 just makes it retry the same unusable event. Unknown thread or duplicate id
  → `None`, not an exception.
- **A failed delivery binds no correlation**, or a later reply would be routed to a message that
  never arrived.
- **Losing the button must not lose the message** — if HS-103 cannot render actions, the text is
  still delivered.
- **Deep links are structured payloads, not URLs in prose.** A link carrying a price in its query
  string is still a claim, and prose URLs bypass M10.

**Open questions — current state:**

- **§10.1 (REST / websocket / widget SDK; does it support proactive injection?)** — unanswered and
  needs the HS-103 team. This is the module's central unknown: if HS-103 *cannot* push proactively,
  §8's fallback (badge / next-open) becomes the primary path, which the `queued` delivery status
  already models.
- **§10.2 (rich actions and read receipts?)** — unanswered. Both are implemented optimistically and
  degrade safely if unsupported.
- **§10.3 (auth model)** — unanswered; no auth is wired to the adapter yet, since the surface
  determines its shape.

---

## 1. Purpose & scope

Deliver the finalised message to the customer through the **existing HS-103 chat interface**, in
the context they are already in (AD), and capture the customer's replies back into the conversation
loop (M12). This is the transport/adapter layer between our backend and the existing UI.

**In scope:** delivery adapter to HS-103, proactive message injection, deep-link payloads, inbound
reply capture, presence/session mapping, delivery receipts.
**Out of scope:** message content (M08/M09/M10), response logic (M12).

## 2. Functional requirements
- Push a **proactive** message into the customer's active HS-103 session (not wait for them to open
  chat) — respecting UX rules (don't interrupt intrusively; open subtly).
- Carry **deep-link actions** (e.g. "resume your booking at the payment step") as structured
  message affordances the UI can render as buttons/links.
- Support localisation and any rich elements HS-103 offers (quick replies, links).
- Capture inbound replies and forward to M12 with conversation correlation.
- **Delivery receipts / read status** where HS-103 supports it → feeds engagement metric (M14).
- Map our `conversation_id` ↔ HS-103 session/thread id.

## 3. Technical design

### 3.1 Integration surface (confirm with HS-103 team — open question)
- Likely one of: REST push API, websocket, or an embedded widget SDK event bus.
- Define adapter `deliver(conversation_id, message, actions) -> delivery_ref` and an inbound
  webhook/subscription `on_customer_message(...) -> forward to M12`.

### 3.2 Proactive injection
- Presence check: is the customer's HS-103 session active? If yes, inject; if the widget is closed,
  use HS-103's notification affordance (badge/toast) per its capabilities.
- Anti-nag: respect frequency caps already enforced upstream (M05) + any UI-level display rules.

### 3.3 Deep links
- Structured action payloads referencing the prior search/booking step (from the trigger context),
  rendered by HS-103; clicking returns the customer to that exact step (supports node AI in M12).

### 3.4 Correlation & receipts
- Persist mapping conversation_id ↔ hs103_thread_id. Handle delivery ack, read receipt, and
  failure (retry/fallback notification).

## 4. Technology & dependencies
- HS-103 API/SDK (TBD), webhook receiver (FastAPI), Postgres/Redis for id mapping, M08/M12.

## 5. Task breakdown
1. Confirm HS-103 integration surface + auth with owning team.
2. Delivery adapter (proactive push + actions/deep links).
3. Inbound reply webhook/subscription → M12.
4. conversation↔thread id mapping store.
5. Delivery/read receipts → M14 engagement signal.
6. UX rules for proactive injection (presence, anti-nag).
7. Localisation + rich-element support.

## 6. Acceptance criteria
- A finalised message appears proactively in the customer's HS-103 session in context.
- Deep-link actions render and return the customer to the referenced step.
- Customer replies are captured and correlated to the right conversation and reach M12.
- Delivery failures are detected and handled (retry/fallback), and receipts feed M14.

## 7. Testing strategy
- Integration against HS-103 sandbox: proactive push, actions, inbound replies, receipts.
- Correlation tests (multiple concurrent conversations map correctly).
- UX review of proactive injection behaviour.

## 8. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| HS-103 capabilities unknown/limited | early discovery with UI team; adapter abstraction |
| Proactive messages feel intrusive | presence + anti-nag + upstream caps; product UX review |
| Reply correlation errors | robust id mapping + idempotent inbound handling |
| No proactive-push capability in HS-103 | negotiate feature or fall back to badge/next-open |

## 9. Effort & sequencing
Phase 2, with M08. ~2–3 weeks + HS-103 team dependency.

## 10. Open questions
1. HS-103 integration surface: REST / websocket / widget SDK? Does it support proactive injection?
2. Does HS-103 support rich actions (buttons/deep links) and read receipts?
3. Auth model between our backend and HS-103?
