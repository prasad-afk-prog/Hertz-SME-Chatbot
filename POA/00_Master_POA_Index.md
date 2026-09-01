# HFB Proactive AI Chatbot — Master POA Index

**Project:** HFB (Hertz for Business) Proactive AI Chatbot
**Source of truth:** `Docs/flowchart.txt`, `Docs/HFB_Chatbot_Flow_Diagram_Explainer.pdf`
**Prepared for:** Envigo delivery team
**Status:** Draft v0.1 — 2026-08-24

---

## 1. What this system does

The HFB portal already knows who the customer is (they are logged in). This system watches
their **behaviour** after login, and when that behaviour matches a trackable pattern (searched
but didn't book, viewed rates and stalled, abandoned a booking, hit an error, dwelt too long,
etc.), it proactively opens a **contextual, verified chatbot conversation** to nudge them toward
completing the booking — or hands them to a human agent when the bot cannot help.

Every claim the bot makes about **price, rate or availability** is verified against the live
booking API before it reaches the customer. Every outcome (conversion, handoff, engagement or
lack of it) feeds an admin-facing reporting dashboard, and admins can create/enable/disable/tune
triggers with no code change.

## 2. Architecture at a glance

```
Portal (post-login)                 Backend Event & Trigger Pipeline (Python)
  behavioural signals  ──►  Ingestion API ─► Event Store ─► Trigger Eval Engine
                                                                  │
                              ┌───────────────┬───────────────────┤
                         in-session      deferred            handoff event
                              │               │                   │
                    Frequency/Precedence  Pending-Engagement   Human Handoff
                              │            Queue + Celery         Manager
                         Fire engagement       │                   │
                              │            (re-eval at login)  human queue
                              ▼
                    Conversation Orchestrator
                    (context + personalisation)
                              │
                    LLM + Fallback ─► Claim Verification ─► Chatbot UI (HS-103)
                              │
                    Customer Response (multi-turn) ─► booking / handoff
                              │
                    Admin, Audit & Reporting  ◄─ every outcome
```

## 3. Technology baseline (proposed)

| Concern | Choice |
|---------|--------|
| Language / API | Python 3.11+, **FastAPI**, Pydantic v2 |
| Durable storage | **PostgreSQL** (events, config, conversations, audit) |
| Streaming / low-latency | **Redis Streams** (event fan-out), Redis (caching, frequency counters) |
| Async / scheduling | **Celery** + **Celery Beat** (deferred sweep, expiry) |
| LLM | Provider-agnostic adapter (Anthropic Claude default) |
| Delivery surface | Existing **HS-103** chatbot UI |
| External systems | HFB portal/booking widget, live **Booking API**, existing support/agent queue |
| Observability | Structured logging, metrics, tracing (see M15) |

## 4. Module catalogue (the "full fan-out")

Each module has its own POA file in this folder. Build order / dependency is indicated.

| POA file | Module | Flow nodes | Depends on |
|----------|--------|-----------|-----------|
| `01_POA_Customer_Journey_Event_Capture.md` | Customer Journey & Behavioural Event Capture | A–J | 02 (contract) |
| `02_POA_Event_Ingestion_API.md` | Event Ingestion API (FastAPI) | K | 03 |
| `03_POA_Event_Store.md` | Event Store (Postgres + Redis Streams) | L | — |
| `04_POA_Trigger_Evaluation_Engine.md` | Trigger Evaluation Engine | M, N | 03, 05, 13 |
| `05_POA_Frequency_Cap_Precedence.md` | Frequency Cap & Precedence Engine | O, Z1 | 03, 13 |
| `06_POA_Pending_Engagement_Queue_Scheduler.md` | Pending-Engagement Queue & Deferred Scheduler | P, R, S, Z2 | 03, 04 |
| `07_POA_Human_Handoff_Manager.md` | Human Handoff Manager | HM, Z3 | 04, 13 |
| `08_POA_Conversation_Orchestrator.md` | Conversation Orchestrator (context + personalisation) | Q, T, U, V | 03, 09, 10, 11 |
| `09_POA_LLM_Integration_Fallback.md` | LLM Integration & Fallback Service | W, X, Y | 08 |
| `10_POA_Claim_Verification_Service.md` | Claim Verification Service | AA, AB, AC | Booking API |
| `11_POA_Chatbot_UI_Integration_HS103.md` | Chatbot UI Integration (HS-103) & Delivery | AD | HS-103 |
| `12_POA_Customer_Response_Conversation_Manager.md` | Customer Response & Multi-turn Conversation Manager | AE–AK | 08, 04, 07 |
| `13_POA_Admin_Console_Trigger_Config.md` | Admin Console & Trigger Configuration | AP, AQ | 03 |
| `14_POA_Audit_Reporting_Analytics.md` | Audit, Reporting & Analytics | AL–AO | 03, 13 |
| `15_POA_Platform_Infra_Security_Observability.md` | Platform: Infra, Security & Observability | cross-cutting | — |
| `16_Test_Dataset_Strategy.md` | Synthetic test-dataset strategy (how to test the whole system with dummy data) | all modules | 02, 10 contracts |
| `17_Mock_Dataset_Audit.md` | Mock-dataset audit vs the Hertz SME use case + v0.2 remediation (business domain, catalogues, lifecycle) | all modules | 16, models |
| `18_Team_Work_Split_and_Git_Workflow.md` | Two-person work split (Track A / Track B), cross-track contract points, daily branch-and-merge workflow, live status tracker | process | 00 |

## 5. Recommended build sequence (phased)

**Phase 0 — Foundations:** M15 (platform skeleton, CI/CD, secrets), M03 (Event Store), M13 (config data model + admin CRUD).

**Phase 1 — Signal to trigger:** M01 (event capture SDK), M02 (Ingestion API), M04 (Trigger Eval), M05 (frequency/precedence). *Outcome: a live signal produces an approved "fire" decision (no message yet).*

**Phase 2 — Conversation:** M08 (Orchestrator), M09 (LLM + fallback), M10 (claim verification), M11 (HS-103 delivery), M12 (response loop). *Outcome: end-to-end proactive conversation for in-session triggers.*

**Phase 3 — Deferred & handoff:** M06 (pending queue + Celery), M07 (handoff manager). *Outcome: deferred triggers and human escalation work.*

**Phase 4 — Visibility:** M14 (audit + reporting), hardening across all modules.

## 6. Cross-cutting principles (apply to every module)

- **Truthfulness:** no price/rate/availability claim reaches a customer unverified (M10).
- **Config-not-code:** triggers, caps, wait periods, routing rules are admin-tunable (M13).
- **Auditability:** every config change and every engagement outcome is logged (M14).
- **Graceful degradation:** if the LLM or a dependency is down, fall back to a safe templated message (M09) — never fail silently to the customer.
- **Idempotency:** event ingestion and trigger firing must tolerate retries/duplicates.
- **PII discipline:** customer data is handled per M15 security section; minimise what leaves the boundary to the LLM.

## 7. Global open questions (to resolve before Phase 1)

1. Which LLM provider/model and hosting (region/data-residency constraints for Hertz)?
2. Is the "Booking API" a single service or several (search vs. rate vs. availability)? Latency SLA?
3. What is the HS-103 integration surface — REST, websocket, embedded widget SDK?
4. Where does the existing "support/agent queue" live (Zendesk, Salesforce, in-house)?
5. Data residency / retention requirements for event and conversation data.
6. Definition of the "dormancy period" and per-trigger default wait/cap values.

> Each module POA repeats its own local open questions in its final section.
