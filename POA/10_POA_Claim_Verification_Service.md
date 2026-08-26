# POA — Claim Verification Service

**Module ID:** M10 | **Flow stage:** 3 | **Flow nodes:** AA, AB, AC | **Status:** Draft
**Depends on:** live Booking API | **Called by:** M08/M12 | **Feeds:** M11 (verified response)

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
