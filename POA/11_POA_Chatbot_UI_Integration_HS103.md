# POA — Chatbot UI Integration (HS-103) & Delivery

**Module ID:** M11 | **Flow stage:** 3 | **Flow nodes:** AD | **Status:** Draft
**Depends on:** existing HS-103 chat interface | **Called by:** M08 | **Feeds:** M12 (customer replies)

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
