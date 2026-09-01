# POA — Claim Verification Service

**Module ID:** M10 | **Flow stage:** 3 | **Flow nodes:** AA, AB, AC
**Status:** **Phase-1 implementation landed 2026-09-01** (Shagun, Track B) — see §11
**Depends on:** live Booking API | **Called by:** M08/M12 | **Feeds:** M11 (verified response)

---

## 11. Implementation status (2026-09-01)

Code: `services/conversation/claim_verification/` — `detection.py`, `client.py`, `service.py`.
Tests: `tests/test_claim_verification_service.py` (23 tests; 144 in the suite, all green).

> **Layout caveat:** the `services/…` tree follows the assumption recorded in POA/18 §8.2, which
> Prasad has not yet confirmed. The tree is deliberately shallow so a rename is one `git mv`.

**All seven §5 tasks are now implemented (2026-09-01):**

| # | Task | Status |
|---|------|--------|
| 1 | Claim contract with M09 (structured tagging) | ✅ `TaggedClaimDetector`; M09 now emits the tags |
| 2 | Fallback pattern detector | ✅ shipped, known-incomplete by design |
| 3 | Booking API client + auth + circuit breaker | ✅ `HTTPBookingAPIClient` — bearer / API-key / HMAC |
| 4 | Verification + tolerance rules | ✅ `TolerancePolicy` — five selectable modes |
| 5 | Resolution / rewrite policy | ✅ delegates to `reference.apply_verification` |
| 6 | Short-TTL cache | ✅ keyed on (kind, location, class, **date**) |
| 7 | Verification logging → M14 | ✅ `VerificationRecord`, now carrying the tolerance rule |

### The HTTP client (task 3)

`services/conversation/claim_verification/http_client.py`.

**§10.1 is still open, so the endpoint shape is an explicit assumption** confined to two seams —
`BookingAPIEndpoints` (paths, query names, response fields) and the two parse methods. Adapting to
the real contract is a config change plus two functions, not a rewrite; a test proves a completely
different endpoint shape works without touching the client.

Transport is injected, with a stdlib `UrllibTransport` behind it — no new dependency for a handful
of GETs, and tests drive 500s, timeouts and malformed bodies through a stub with no socket and no
sleeping. Every failure maps onto the three exceptions the service already handles, so the breaker
and cache behave identically against the mock or a live API. Note the asymmetry: **a malformed 200
is an outage** (it counts towards the breaker) while **a 404 is a bad lookup** (it does not) — a
broken dependency and a missing key are different operational facts.

**Auth covers all three plausible models** (§10.3 is open): bearer, API key, and HMAC request
signing, selected from the environment and never taken as literals. `__repr__` is overridden on
every auth type, because a config object in a stack trace would otherwise print the secret.

### Tolerance policy (task 4)

**§10.2 is a product decision that is still open.** Rather than leave a bare `Decimal`,
`TolerancePolicy` implements the plausible policies as selectable modes — `exact`, `absolute`,
`percentage`, `rounded`, `at_least` — so answering §10.2 becomes a config change.

`at_least` is the one that is easy to get backwards. "From £42/day" promises the customer can have
it *for* £42, so the claim holds when the live price is at or below that and **fails when the real
price is higher**. A customer quoted "from £42" and charged £55 was misled; one charged £38 was not.

The default stays deliberately strict (absolute, £0.01): a tolerance that is too tight corrects a
correct price, which is harmless; one that is too loose lets a wrong price reach a customer, which
is the failure this module exists to prevent. Every `VerificationRecord` now carries
`tolerance_rule`, because a verification outcome is only meaningful next to the rule that produced
it.

**Design decisions worth carrying forward:**

- **Resolution is delegated, not reimplemented.** `reference.apply_verification` is the executable
  spec, and `test_invariants.py` / `test_golden_scenarios.py` already assert against it. A second
  copy in the service would mean those suites stopped covering what ships. A test pins that the
  service and the reference agree.
- **The fallback detector's gaps are pinned by test**, not waved away — it misses
  words-not-digits ("forty-two pounds") and amounts without a currency symbol. §8's first risk is
  detector brittleness, and a fallback that quietly under-detects is worse than none because it
  looks like coverage. This is why tagged claims are primary.
- **Outage vs no-data are distinguished in the log** (`FailureKind`). Both are UNVERIFIABLE to the
  customer, but M14 needs to tell a broken dependency from a bad lookup. `VerifyStatus` was **not**
  widened — it is the shared contract in `models.py` that `apply_verification` branches on.
- **A bad lookup does not trip the breaker.** Counting `no_data` as failure would open the circuit
  on healthy infrastructure.
- **Failures are never cached.** A cached outage would poison verification for the whole TTL even
  after the API recovered.
- **A draft quoting money we cannot even look up is refused, not delivered** (`blocked=True`).
- **The §3.3 guardrail counts token occurrences rather than testing containment.** A correction may
  legitimately contain the token it replaced — "available" → "not currently available" — and a
  naive `token in delivered` check flags a correct rewrite as a failure. This was caught by its own
  test during implementation.

**Open questions — current state:**

- **§10.1 (endpoints / latency SLA)** — still unanswered, but no longer blocking. The client is
  built against a documented assumption isolated in `BookingAPIEndpoints`; the real contract is a
  config change. The 1s inline timeout is a placeholder for the real SLA.
- **§10.2 (tolerance policy)** — still a product decision, but no longer blocking: all five modes
  are implemented and selectable. Product picks one; nobody writes code.
- **§10.3 (structured claim tags)** — **answered: yes.** M09's `AnthropicProvider` emits claims via
  `output_config.format`, so the exact path is real rather than aspirational.

---

## 1. Purpose & scope

The truthfulness guarantee. Detect whether a draft response references **price, rate or
availability** (AA); if so, verify the claim against the **live booking API** (AB) before it is
allowed to reach the customer; produce the **verified response** (AC). If not, pass through (AC).

**In scope:** claim detection/extraction, live verification against booking API,
correction/redaction of unverifiable or wrong claims, verification result logging.
**Out of scope:** generating the text (M09), delivering it (M11).

## 2. Functional requirements
- **Claim detection (AA):** identify statements asserting a price/rate/availability (numbers,
  "available", "from £X", specific vehicle at specific dates, etc.).
- **Extraction:** structure each claim (route, dates, vehicle class, quoted price/availability) for
  a booking-API lookup.
- **Verification (AB):** query the live booking API and compare claim vs reality within tolerance.
- **Resolution (AC):**
  - claim correct → pass through;
  - claim wrong → correct it with the live value, or redact/soft-phrase and (optionally) deep link;
  - not verifiable (no data / API down) → remove the specific claim / degrade to a safe phrasing;
- Never let an **unverified** price/rate/availability claim reach the customer.
- Log every claim, the verification outcome, and any correction (for audit/M14 and quality).

## 3. Technical design

### 3.1 Claim detection
- Layered: (a) fast pattern/NER pass for currency/availability phrasing; (b) optional structured
  generation — have M09 emit claims as structured metadata alongside prose so detection is exact,
  not regex-guessed. **Recommended:** require M09 to tag factual claims (contract with M08/M09) to
  avoid brittle post-hoc parsing.

### 3.2 Verification
```python
class BookingClaim(BaseModel):
    kind: Literal["price","rate","availability"]
    route: Route; dates: Dates; vehicle_class: str | None
    quoted_value: Decimal | bool | None
async def verify(claim) -> VerifyResult:  # OK | WRONG(correct_value) | UNVERIFIABLE
```
- Call booking API (search/rate/availability endpoints). Cache short-TTL identical lookups to cut
  latency/load. Tolerance rules for price (exact vs rounded) defined with product.

### 3.3 Resolution & guardrails
- Rewrite policy: prefer correcting with live value; if not possible, remove the claim while keeping
  the helpful intent (and deep-link to live results). Corrections themselves must not introduce new
  unverified claims.
- Hard rule: `UNVERIFIABLE` or `WRONG-and-uncorrectable` ⇒ strip the claim.

### 3.4 Latency
- Verification is inline before delivery → tight timeout; on booking-API timeout treat as
  UNVERIFIABLE (strip claim) rather than blocking or sending unverified.

## 4. Technology & dependencies
- Booking API client (auth, retries, circuit breaker), Redis (short-TTL cache), M08/M09 contract
  for tagged claims.

## 5. Task breakdown
1. Claim contract with M09 (structured claim tagging).
2. Fallback pattern/NER detector for untagged claims.
3. Booking API client + auth + circuit breaker.
4. Verification + tolerance rules.
5. Resolution/rewrite policy engine.
6. Short-TTL cache.
7. Verification logging → M14/audit.

## 6. Acceptance criteria
- Any response asserting a price/rate/availability is either verified-correct, corrected to the
  live value, or has the claim removed — **no unverified claim is ever delivered** (proven by
  adversarial tests).
- Non-factual responses pass through unchanged.
- On booking-API outage, claims are stripped and a safe response still delivers.
- Every verification is logged with claim, outcome, correction.

## 7. Testing strategy
- Unit: detector precision/recall on a labelled claim corpus; resolution policy.
- Integration: verify against a booking-API sandbox incl. mismatch and outage.
- **Adversarial/red-team:** attempt to get an unverified price through; must fail.

## 8. Risks & mitigations
| Risk | Mitigation |
|------|-----------|
| Detector misses a claim (regex brittle) | M09 structured claim tagging as primary path |
| Booking API slow → latency breach | tight timeout + treat as unverifiable + cache |
| Correction introduces new claim | corrections routed through the same verifier |
| Tolerance mismatch (rounding) | product-agreed tolerance rules |

## 9. Effort & sequencing
Phase 2, with M08/M09. ~3 weeks. High priority — core trust guarantee.

## 10. Open questions
1. Booking API endpoints/latency SLA for price/rate/availability lookups?
2. Price tolerance policy (exact match vs rounded/"from")?
3. Will M09 emit structured claim tags, or must detection be inferred?
