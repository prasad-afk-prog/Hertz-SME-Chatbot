# 17 — Mock Dataset Audit & Remediation (Hertz SME Chatbot)

**Date:** 2026-08-26
**Scope reviewed:** `generator/`, `mocks/`, `test_data/` — the full synthetic dataset and the code that emits it.
**Business context:** Hertz SME / business car rental (UK), post-login conversational chatbot.
**Companion:** an interactive version of this audit is published at
`https://claude.ai/code/artifact/b27a356d-893a-4e15-92f9-2634877b337e`.

> **Status:** the **Critical**, **High** and **Medium** gaps below were implemented in
> generator **v0.2** (this commit). The remaining work is the *future-client-compatibility*
> layer (§7), which is a code/architecture task, not a data task.

---

## 1. Overall suitability

| | |
|---|---|
| **Verdict** | Partially Suitable → **now Suitable for the demo** after v0.2 |
| **Score at audit** | **5.5 / 10** (blended) |
| **Why blended** | ~9/10 as a *trigger + claim-verification* test harness (its original purpose); ~3.5/10 as a *conversational SME assistant* before v0.2 |

The dataset was engineered around one guarantee — *no price/rate/availability claim reaches a
customer unverified* — and it does that superbly. But the questions a business customer actually
asks (booking changes, policies, mileage, fuel, insurance, deposits, account/company) describe a
transactional Q&A assistant whose core entities were **not modelled**. The gap was data *surface
area*, not data *quality* — so the fix was to **extend, not rebuild**.

---

## 2. Structure review (at audit)

**Entities present:** `locations` (6), `vehicle_classes` (5), `rate_cards`, `availability`,
`customers` (1,000), `bookings` (1,513), `events` (4,014), `triggers`/`routing_rules`, 7 golden
scenarios. All derived from one seeded world (`seed=42`), validated by the Pydantic contract
models in `generator/models.py`.

**What held up well**
- Clean referential integrity (every booking/event `customer_id` resolves; contexts use valid keys).
- One world backs both the claims and the booking-API mock — the property that makes M10 airtight.
- Money modelled as `Decimal`; `extra="forbid"` on every model makes malformed input a real test.

**Structural issues found**
- Bookings inert: all `status:"completed"`, all in the past, all `pickup == dropoff`.
- Booking `total` was a flat `£45 × days`, not derived from the rate-card world.
- Reference data hard-coded in `world.py` (hostile to a future swap).
- Currency always `GBP`, including the EUR markets (FRA/CDG/MAD).

---

## 3. Hertz SME business-context validation

**Realistic:** ACRISS class codes (`ECAR/CCAR/ICAR/FCAR`), a plausible airport hub set,
weekend/location rate multipliers, GDPR consent flags, SME concept in spirit
(`customer_type: SME`, a `negotiated_rate_plan` reference).

**Unrealistic / off-context (at audit):** individuals dominated an SME product;
`negotiated_rate_plan` was a bare string that resolved to nothing; **no vans/LCVs**;
**no one-way rentals**; vehicle class lacked real attributes; `SUV` isn't a valid ACRISS code.

---

## 4. Chatbot test-coverage matrix (at audit)

| User scenario | Supported? | Missing | v0.2 status |
|---|---|---|---|
| Vehicle availability | Yes | narrow window; no vans | ✅ vans added |
| Vehicle / category selection | Partial | seats/doors/transmission/fuel/deposit/min-age | ✅ enriched |
| Location pickup / drop-off | Partial | address, hours, one-way | ✅ address/hours/currency; one-way fee in rate card |
| Rental dates & times | Partial | out-of-hours | ⚪ opening_hours added; time variation deferred |
| Pricing / rates | Partial | weekly/tax/deposit/one-way/real SME rate | ✅ all added; plan discount now resolves |
| Business / customer info | Partial | company, account, cost centre, contact | ✅ Company entity |
| Booking status / details | Partial | active/upcoming/cancelled, reference no. | ✅ lifecycle + reference_no |
| Modify / cancel a booking | **No** | editable bookings + cancellation policy | ✅ mutable statuses + `cancellation` |
| Rental policies & restrictions | **No** | age, licence, cross-border, late return | ✅ `policies` catalogue |
| Mileage / fuel | **No** | allowance, fuel policy | ✅ policies + `mileage_policy` on class |
| Insurance / protection | **No** | CDW/excess/PAI/tyre-windscreen | ✅ `protection_products` catalogue |
| Deposits | **No** | deposit per class + terms | ✅ deposit on class/rate/booking + policy |
| Extras / add-ons | **No** | additional driver, GPS, child seat, FPO | ✅ `extras` catalogue |
| Account / company queries | **No** | invoices, PO, credit terms | ✅ `companies` + `invoices` |

Tally at audit: **1 yes · 6 partial · 7 no**. After v0.2: the seven "no" rows are all backed by data.

---

## 5. Prioritised gaps → v0.2 resolution

**Critical (all implemented)**
- **Company / Account** → `Company` model + `master/companies.jsonl`; customers carry `company_id`.
- **Booking lifecycle** → `BookingStatus` (upcoming/active/completed/cancelled), future-dated bookings, `reference_no`.
- **Negotiated rate resolution** → `RatePlan` catalogue (`SME-STD-2026` 10%, `corporate-STD-2026` 15%); the existing `negotiated_rate_plan` string now points at a real, priced plan and drives booking totals.
- **Policy catalogue** → `Policy` model + `world/policies.json` (mileage, fuel, deposit, cancellation, driver age, cross-border, late return).
- **Extended rate model** → `RateCard` gains `weekly_rate`, `deposit`, `tax_rate` (VAT), `one_way_fee`, per-location `currency`.

**High (all implemented)**
- **Vehicle attributes** → seats, doors, transmission, fuel_type, luggage, deposit, min_driver_age, mileage_policy.
- **Van / LCV classes** → `PVAN` (Panel Van), `LVAN` (Luton Van), `category="van"`.
- **Protection products** → `world/protection_products.json` (CDW, TP, Super Cover, PAI, Tyre & Windscreen).
- **Extras** → `world/extras.json` (Additional Driver, Sat Nav, Child Seat, FPO, Winter Tyres).
- **Location enrichment** → address, opening hours, type, currency.
- **Drivers** → `BookingDriver` (age, licence_held_years) on each booking.

**Medium (implemented)**
- Per-location currency (EUR at FRA/CDG/MAD). ✅
- Real booking totals derived from the rate world (1,600+ distinct values, was ~7). ✅
- One-way support via `one_way_fee`. ✅ (data still defaults pickup=dropoff; one-way *bookings* deferred.)
- Invoices roll up completed bookings per company (`master/invoices.jsonl`). ✅

**Optional (deferred — not blocking the demo):** promo/discount codes, loyalty points detail,
young-driver surcharge modelling, damage/incident history, multi-currency FX on invoices,
out-of-hours time variation, explicit one-way *bookings*.

---

## 6. What v0.2 added — at a glance

- **6 new models' worth of entities:** `Company`, `RatePlan`, `Invoice`(+`InvoiceLine`),
  `ProtectionProduct`, `Extra`, `Policy`, plus `Contact`, `BookingDriver`, `Cancellation`.
- **Enriched existing models:** `Location`, `VehicleClass`, `RateCard`, `Customer`, `Booking`.
- **New generator modules:** `catalogues.py` (static reference), `business.py` (companies + invoices).
- **New generated files:** `world/{protection_products,extras,policies}.json`,
  `master/{rate_plans,companies,invoices}.jsonl`.
- **Tests:** `tests/test_business_entities.py` (10 tests) — schema validity, referential integrity,
  lifecycle coverage, derived totals. **Suite is 43 tests, all green.** The 7 golden
  verification scenarios and their pinned prices are **unchanged** (car rate-card RNG sequence preserved).

Additions to the original signal-pipeline models are optional-with-defaults, so the verification
harness keeps validating unchanged.

---

## 7. Future client-dataset compatibility (remaining work)

Goal: when the real client data arrives, we should only need to **(a)** drop files in, **(b)** adjust a
field-map, **(c)** tweak config — never rewrite business logic. Three thin layers deliver that:

1. **Repository interface** — the chatbot depends on `RentalRepository.get_rate()/get_booking()/get_policy()…`, never on file paths. `mocks/booking_api.py` is already a partial version; formalise it.
2. **Loader + `field_map.yaml`** — maps source column → canonical model field, so a renamed client column is a YAML edit, not code.
3. **Lenient edge, strict core** — keep `extra="forbid"` on internal models, but add a lenient inbound DTO (`extra="ignore"`) → adapter → strict model, so real files with extra columns aren't rejected.
4. **Externalise reference data** — move the hard-coded locations/classes/base-rates out of `world.py` into data files.

Keep `generator/models.py` as the canonical contract — it is already the best compatibility asset.

---

## 8. Final recommendation

**Modify first — do not rebuild.** Done in v0.2: the verification harness is untouched, the SME
business domain is now modelled, and the demo can answer the full set of post-login queries. **Continue
development on this dataset.** Schedule §7 (the repository/field-map/DTO layer) *before* wiring the real
client dataset in, so the eventual production swap is data + config, not code.
