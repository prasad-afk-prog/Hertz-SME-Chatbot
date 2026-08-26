# POA — Platform: Infrastructure, Security & Observability

**Module ID:** M15 | **Flow stage:** cross-cutting | **Status:** Draft
**Depends on:** — (foundational) | **Consumed by:** all modules

---

## 1. Purpose & scope

The shared foundation every module builds on: service skeleton, environments, CI/CD, secrets,
data protection/privacy, authn/authz, observability (logging/metrics/tracing/alerting), and the
non-functional guarantees (latency, availability, scale). Building this first prevents each module
reinventing it.

**In scope:** repo/service scaffolding, infra & deploy, secrets, security/PII, observability, SRE
concerns.
**Out of scope:** business logic (the functional modules).

## 2. Functional / non-functional requirements
- **Service skeleton:** a shared FastAPI/Celery template (config, health, metrics, logging, error
  handling, DB/Redis clients, testing harness) reused by M02/M04/M07/M08/M09/M10/M11/M13/M14.
- **Environments:** local (docker-compose), dev, staging, prod; parity and repeatable provisioning.
- **CI/CD:** lint, type-check, tests, contract tests, build, migrate, deploy; per-PR checks.
- **Secrets/config:** central secret store; no secrets in code; per-env config.
- **Security & privacy** (see §4).
- **Observability** (see §5).
- **NFRs:** define latency budgets (fire→deliver), availability targets, scale/throughput, and
  degradation behaviour per module.

## 3. Technical design — infra
- Containerised services (Docker); orchestration TBD (Kubernetes / ECS / the Hertz standard).
- Managed Postgres + Redis; Celery workers + Beat as separate deployables.
- IaC (Terraform) for reproducibility; blue/green or rolling deploys; DB migrations gated in CI.
- Config feedback loop (M13) infra: Redis pub/sub or similar for hot-reload.

## 4. Security & data privacy (critical for Hertz customer data)
- **AuthN/AuthZ:** service-to-service auth (mTLS/signed tokens); admin RBAC (M13); portal→API auth
  (M02). Least privilege everywhere.
- **PII discipline:** classify customer data; field allow-lists at ingestion (M02); **minimise/redact
  what is sent to the LLM** (M08/M09); encryption at rest + in transit.
- **Data residency & retention:** enforce region constraints (LLM hosting, storage) and retention
  windows (events M03, conversations, analytics M14). Right-to-erasure support.
- **Auditability:** config-change audit (M13) + access logs; tamper-evident.
- **Consent:** honour marketing/analytics consent captured at M01 throughout the pipeline.
- **Secrets:** vault/secret-manager; rotation; no secrets in logs.
- **Threat model:** prompt injection (M08/M09 guardrails), spoofed events (M02 identity binding),
  data exfiltration via LLM, abuse/rate-limits.

## 5. Observability
- **Structured logging** (correlation id per conversation/event across services).
- **Metrics** (Prometheus): pipeline latency, fire rate, engagement/conversion/handoff, fallback &
  verification rates, LLM latency/cost, queue depths, error rates.
- **Tracing** (OpenTelemetry): trace a signal end-to-end (ingest→fire→generate→verify→deliver).
- **Alerting/SLOs:** on latency breach, LLM/booking-API circuit open, queue backlog, error spikes,
  outbox/relay lag, verification failures.
- **Dashboards:** ops (health/latency) distinct from business reporting (M14).

## 6. Task breakdown
1. Shared service template (FastAPI + Celery) + testing harness.
2. Local docker-compose (Postgres, Redis, services) + dev/staging/prod IaC.
3. CI/CD pipeline (lint, types, tests, contract tests, migrations, deploy).
4. Secret management + per-env config.
5. Security baseline: service auth, RBAC, encryption, PII allow-lists, consent propagation.
6. Data residency/retention + erasure tooling.
7. Observability stack: logging, metrics, tracing, alerting, SLOs.
8. Load/latency test harness + degradation playbooks.

## 7. Acceptance criteria
- A new module can be scaffolded from the template with health/metrics/logging/tracing wired in.
- CI blocks merges on failing lint/types/tests/contracts; deploys run migrations safely.
- No secrets in code; PII allow-lists and encryption verified; consent honoured end to end.
- A single signal is traceable end-to-end; core SLO alerts fire in tests.
- Data residency/retention/erasure controls demonstrably work.

## 8. Testing strategy
- Pipeline dry-runs; security review + pen-test of auth/PII/LLM boundaries.
- Chaos/fault injection (DB/Redis/LLM/booking-API down) → degradation behaviour holds.
- Load tests to validate NFR latency/throughput budgets.

## 9. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Privacy/compliance gap with customer data | early legal/security review, PII-by-design, DPIA |
| Orchestration/hosting undecided delays start | pick the Hertz standard early; template is portable |
| Observability added late = blind debugging | build it into the shared template from day one |
| LLM data-residency non-compliance | choose compliant provider/region before Phase 2 |

## 10. Effort & sequencing
Phase 0 — build first (skeleton, CI/CD, security/observability baseline). Ongoing hardening
through all phases. Initial ~3–4 weeks, then continuous.

## 11. Open questions
1. Hosting/orchestration standard at Hertz (K8s/ECS/other) and cloud/region?
2. Compliance regime (GDPR/UK-GDPR) specifics, DPIA owner, retention mandates?
3. Approved LLM provider + region for data residency?
4. Existing shared platform/tooling at Envigo/Hertz to reuse vs. greenfield?
