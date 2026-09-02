"""Pydantic contract models — the single source of truth (design principle P3).

The real services (M02 ingestion, M10 verification, M13 config …) should import
these same models, so any data the generator emits is schema-valid by
construction and contract tests pass for free.

Money is modelled with Decimal (never float) to match a real booking system.

v0.2 — extended from the pure proactive-signal contract to a full SME
business-rental domain (companies, rate plans, booking lifecycle, extras,
protection, policies, invoices). Additions to the original models are optional
with defaults, so the golden-scenario harness keeps validating unchanged.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class SignalType(str, Enum):
    search_no_convert = "search_no_convert"
    rate_view_no_progress = "rate_view_no_progress"
    booking_abandoned = "booking_abandoned"
    error_hit = "error_hit"
    extended_dwell = "extended_dwell"
    session_ended_no_booking = "session_ended_no_booking"
    repeated_search = "repeated_search"          # deferred (I)
    dormant = "dormant"                          # deferred (J)


class CustomerType(str, Enum):
    individual = "individual"
    SME = "SME"
    corporate = "corporate"


class Segment(str, Enum):
    new = "new"
    occasional = "occasional"
    frequent = "frequent"
    dormant = "dormant"


class BookingStep(str, Enum):
    search = "search"
    select_vehicle = "select_vehicle"
    extras = "extras"
    review = "review"
    payment = "payment"
    confirm = "confirm"


class Source(str, Enum):
    booking_widget = "booking_widget"
    portal = "portal"


class ClaimKind(str, Enum):
    price = "price"
    rate = "rate"
    availability = "availability"


class MessageKind(str, Enum):
    llm = "llm"                # generated, no factual claim
    fallback = "fallback"      # templated (LLM unavailable / low confidence)
    verified = "verified"      # claim checked and correct
    corrected = "corrected"    # claim was wrong, replaced with live value
    stripped = "stripped"      # claim unverifiable, removed


class TerminalState(str, Enum):
    no_engagement = "no_engagement"
    converted = "converted"
    handed_off = "handed_off"


class TriggerType(str, Enum):
    in_session = "in_session"
    deferred = "deferred"


class VerifyStatus(str, Enum):
    ok = "ok"
    wrong = "wrong"
    unverifiable = "unverifiable"


# ---- business-domain enums (v0.2) ----------------------------------------- #
class Transmission(str, Enum):
    manual = "manual"
    automatic = "automatic"


class FuelType(str, Enum):
    petrol = "petrol"
    diesel = "diesel"
    hybrid = "hybrid"
    electric = "electric"


class VehicleCategory(str, Enum):
    car = "car"
    van = "van"


class LocationType(str, Enum):
    airport = "airport"
    city = "city"
    suburban = "suburban"        # S1 (POA/16 §16.2) — downtown/city + suburban stations


class BookingStatus(str, Enum):
    upcoming = "upcoming"
    active = "active"
    completed = "completed"
    cancelled = "cancelled"
    no_show = "no_show"          # S2 (POA/16 §16.1) — customer never collected; no-show fee applies


class PricingUnit(str, Enum):
    per_day = "per_day"
    per_rental = "per_rental"


class PolicyTopic(str, Enum):
    mileage = "mileage"
    fuel = "fuel"
    deposit = "deposit"
    cancellation = "cancellation"
    driver_age = "driver_age"
    cross_border = "cross_border"
    late_return = "late_return"
    no_show = "no_show"          # S2 (POA/16 §16.1)


# ---- fee / dispute enums (S2 — POA/16 §16.1) ------------------------------ #
class FeeType(str, Enum):
    """Itemised charge types a booking can carry — the ones a customer asks
    'why was I charged X?' about. `one_way` is priced at booking; the rest are
    post-rental charges that can be disputed."""
    one_way = "one_way"
    late_return = "late_return"
    no_show = "no_show"
    fuel = "fuel"
    additional_driver = "additional_driver"
    young_driver = "young_driver"
    cross_border = "cross_border"
    other = "other"


class DisputeResolution(str, Enum):
    upheld = "upheld"                      # charge is correct, stands
    refunded = "refunded"                  # charge was wrong, fully reversed
    partial_refund = "partial_refund"      # charge partly reduced to the correct amount
    escalated_to_human = "escalated_to_human"


# --------------------------------------------------------------------------- #
# Reference world
# --------------------------------------------------------------------------- #
class Location(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str
    name: str
    country: str
    region: str
    timezone: str
    # v0.2 enrichment
    type: LocationType = LocationType.airport
    address: str | None = None
    opening_hours: str | None = None
    currency: str = "GBP"


class VehicleClass(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    label: str
    example_model: str
    # v0.2 enrichment
    category: VehicleCategory = VehicleCategory.car
    seats: int | None = None
    doors: int | None = None
    transmission: Transmission | None = None
    fuel_type: FuelType | None = None
    luggage: int | None = None           # large bags
    deposit: Decimal | None = None       # security deposit / pre-auth
    min_driver_age: int | None = None
    mileage_policy: str | None = None    # e.g. "unlimited" | "limited-250mi/day"


class RateCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str
    vehicle_class: str
    date: date
    daily_rate: Decimal
    currency: str = "GBP"
    # v0.2 enrichment
    weekly_rate: Decimal | None = None
    deposit: Decimal | None = None
    tax_rate: Decimal = Decimal("0.20")   # VAT
    one_way_fee: Decimal | None = None


class Availability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str
    vehicle_class: str
    date: date
    available: int


# --------------------------------------------------------------------------- #
# Customer master
# --------------------------------------------------------------------------- #
class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marketing: bool = True
    analytics: bool = True


class Customer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str
    customer_type: CustomerType
    region: str
    language: str
    segment: Segment
    created_at: datetime
    consent: Consent = Field(default_factory=Consent)
    negotiated_rate_plan: str | None = None      # kept: plan id (now resolvable, see RatePlan)
    last_booking_at: datetime | None = None
    # v0.2 — real links into the business layer
    company_id: str | None = None
    rate_plan_id: str | None = None


class BookingDriver(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    age: int
    licence_held_years: int


class Cancellation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fee: Decimal
    deadline: datetime          # free cancellation up to this instant
    policy: str


class FeeLine(BaseModel):
    """An itemised charge on a booking (S2 — POA/16 §16.1). `one_way` is part of
    the quoted `total`; late-return / no-show / fuel are post-rental charges that
    surface on the final statement and can be disputed."""
    model_config = ConfigDict(extra="forbid")
    code: FeeType
    label: str
    amount: Decimal
    currency: str = "GBP"
    disputed: bool = False
    dispute_reason: str | None = None


class Booking(BaseModel):
    model_config = ConfigDict(extra="forbid")
    booking_id: str
    customer_id: str
    pickup: str
    dropoff: str
    vehicle_class: str
    pickup_at: datetime
    return_at: datetime
    total: Decimal
    status: BookingStatus = BookingStatus.completed
    # v0.2 — a booking you can actually query and manage
    company_id: str | None = None
    reference_no: str | None = None
    currency: str = "GBP"
    extras: list[str] = Field(default_factory=list)         # Extra.code refs
    protection: list[str] = Field(default_factory=list)     # ProtectionProduct.code refs
    driver: BookingDriver | None = None
    deposit: Decimal | None = None
    tax: Decimal | None = None                              # VAT component of total
    cancellation: Cancellation | None = None
    # S2 (POA/16 §16.1) — one-way and itemised/disputable charges
    one_way_fee: Decimal | None = None                      # set when dropoff != pickup
    fees: list[FeeLine] = Field(default_factory=list)       # itemised charges (incl. disputes)


# --------------------------------------------------------------------------- #
# Business layer (v0.2): companies, rate plans, invoices
# --------------------------------------------------------------------------- #
class Contact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    email: str
    phone: str


class Company(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: str
    name: str
    customer_type: CustomerType = CustomerType.SME
    account_no: str
    cost_centres: list[str] = Field(default_factory=list)
    credit_terms: str = "30 days"
    rate_plan_id: str | None = None
    primary_contact: Contact
    loyalty_tier: str | None = None


class RatePlan(BaseModel):
    """A negotiated business plan. `discount_pct` applies to standard rate cards;
    `net_daily_rate` (class -> price) overrides with a fixed net rate when set."""
    model_config = ConfigDict(extra="forbid")
    rate_plan_id: str
    name: str
    discount_pct: Decimal | None = None
    net_daily_rate: dict[str, Decimal] = Field(default_factory=dict)
    included_protections: list[str] = Field(default_factory=list)
    included_extras: list[str] = Field(default_factory=list)


class InvoiceLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    booking_id: str | None = None
    description: str
    net: Decimal
    vat: Decimal
    gross: Decimal


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_id: str
    company_id: str
    period: str                 # e.g. "2026-Q2"
    line_items: list[InvoiceLine] = Field(default_factory=list)
    net: Decimal
    vat: Decimal
    gross: Decimal
    currency: str = "GBP"
    po_number: str | None = None
    status: str = "issued"


# --------------------------------------------------------------------------- #
# Catalogues (v0.2): extras, protection, policies
# --------------------------------------------------------------------------- #
class ProtectionProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str
    code: str
    name: str
    daily_price: Decimal
    excess_before: Decimal | None = None    # liability without the product
    excess_after: Decimal | None = None     # liability with the product
    included_by_default: bool = False
    description: str | None = None


class Extra(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extra_id: str
    code: str
    name: str
    pricing_unit: PricingUnit = PricingUnit.per_day
    price: Decimal
    max_qty: int = 1
    description: str | None = None


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str
    topic: PolicyTopic
    applies_to: str = "all"       # "all" | vehicle category | region
    summary: str
    detail: str | None = None


# --------------------------------------------------------------------------- #
# Sessions & events (M02 contract)
# --------------------------------------------------------------------------- #
class EventContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pickup: str | None = None
    dropoff: str | None = None
    pickup_at: datetime | None = None
    return_at: datetime | None = None
    vehicle_class: str | None = None
    step: BookingStep | None = None
    error_code: str | None = None
    dwell_ms: int | None = None


class Event(BaseModel):
    # extra="forbid" makes malformed-event tests meaningful
    model_config = ConfigDict(extra="forbid")
    event_id: str
    customer_id: str
    session_id: str
    signal_type: SignalType
    occurred_at: datetime
    source: Source = Source.booking_widget
    context: EventContext = Field(default_factory=EventContext)
    consent: Consent = Field(default_factory=Consent)
    schema_version: str = "1.0.0"


class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    customer_id: str
    login_at: datetime
    logout_at: datetime | None = None
    device: str = "web"


# --------------------------------------------------------------------------- #
# Config fixtures (M13 -> M04/M05/M07)
# --------------------------------------------------------------------------- #
class FrequencyCap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    per: str = "P7D"       # ISO-8601 duration window
    max: int = 1


class Deferred(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wait_period: str = "PT0S"
    expiry: str = "P3D"


class TriggerMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_type: SignalType
    conditions: list[dict] = Field(default_factory=list)


class TriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger_id: str
    enabled: bool = True
    match: TriggerMatch
    type: TriggerType = TriggerType.in_session
    deferred: Deferred | None = None
    frequency_cap: FrequencyCap = Field(default_factory=FrequencyCap)
    precedence: int = 100
    message_template_ref: str | None = None


class RoutingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    match: dict = Field(default_factory=dict)
    route: dict = Field(default_factory=dict)
    sla: dict | None = None
    fallback_queue: str | None = None


# ---- engagement decision (A6/M05 -> M08) — POA/05, POA/18 §5 -------------- #
# NOTE (cross-track): the reserve->confirm/rollback handshake is shared with
# Track B's M08. Shagun has a local POA/18 §5 edit for it; reconcile these names
# with that when it lands.
class SuppressionReason(str, Enum):
    frequency_cap = "frequency_cap"       # per-trigger cap hit
    global_cap = "global_cap"             # per-customer global cap hit
    cooldown = "cooldown"                 # inside the global quiet period
    precedence_loss = "precedence_loss"   # lost precedence arbitration


class MatchCandidate(BaseModel):
    """One in-session trigger match handed to M05 for cap/precedence arbitration."""
    model_config = ConfigDict(extra="forbid")
    trigger: TriggerConfig
    signal_at: datetime


class EngagementDecision(BaseModel):
    """M05's verdict. `reservation_id` is the handle M08 confirms (on delivery) or
    rolls back (on failure), so a failed send never burns the customer's cap —
    the reserve->confirm/rollback contract (POA/05 §3.2)."""
    model_config = ConfigDict(extra="forbid")
    approved: bool
    customer_id: str
    reservation_id: str | None = None
    winner_trigger_id: str | None = None
    suppression_reason: SuppressionReason | None = None                # set when not approved
    losers: dict[str, SuppressionReason] = Field(default_factory=dict)  # trigger_id -> reason (M14)


class FireMessage(BaseModel):
    """A5/M04 -> M08/B2: start a conversation for an approved engagement. Carries
    A6's reservation_id so M08 confirms (on delivery) / rolls back (on failure)."""
    model_config = ConfigDict(extra="forbid")
    reservation_id: str
    customer_id: str
    trigger_id: str
    event_id: str
    message_template_ref: str | None = None
    occurred_at: datetime


# --------------------------------------------------------------------------- #
# LLM fixtures & claims (M09 <-> M10)
# --------------------------------------------------------------------------- #
class BookingClaim(BaseModel):
    """A factual assertion the model made, tagged for exact verification.

    `text_token` is the EXACT substring in the response that expresses the
    claim, so resolution (correct/strip) is deterministic — never regex-guessed.
    """
    model_config = ConfigDict(extra="forbid")
    kind: ClaimKind
    pickup: str
    dropoff: str
    pickup_at: datetime
    return_at: datetime
    vehicle_class: str
    quoted_price: Decimal | None = None
    quoted_available: bool | None = None
    text_token: str


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    claims: list[BookingClaim] = Field(default_factory=list)
    confidence: float | None = None
    finish_reason: str = "stop"


class VerifyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: VerifyStatus
    correct_token: str | None = None       # replacement phrasing when wrong
    correct_price: Decimal | None = None
    correct_available: bool | None = None


# --------------------------------------------------------------------------- #
# Golden scenarios & expected outcomes
# --------------------------------------------------------------------------- #
class FailureKey(BaseModel):
    """Forces the booking-API mock to fail for a (location, class, date)."""
    model_config = ConfigDict(extra="forbid")
    location_id: str
    vehicle_class: str
    date: date


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fired: bool
    suppressed_reason: str | None = None
    message_kind: MessageKind | None = None
    terminal_state: TerminalState | None = None
    # values that must NOT appear in the delivered text (e.g. a wrong price)
    delivered_excludes: list[str] = Field(default_factory=list)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    description: str
    seed: int
    customer: Customer
    trigger: TriggerConfig
    events: list[Event]
    llm_response: LLMResponse | None = None
    llm_timeout: bool = False            # force M09 unavailable -> fallback
    replies: list[str] = Field(default_factory=list)
    resolves: bool | None = None         # AH branch: does the bot resolve?
    booking_api_failures: list[FailureKey] = Field(default_factory=list)
    prior_engagements: list[datetime] = Field(default_factory=list)  # for cap
    expected: Expected


# --------------------------------------------------------------------------- #
# Fee-dispute fixtures (S2 — POA/16 §16.1: "why was I charged X?" / disputes)
#
# Hand-authored, deterministic fixtures parallel to the golden Scenario tier but
# for the fees/charges domain rather than the proactive trigger pipeline. Each
# pins a booking's billed fee against the rule-correct amount and the expected
# dispute resolution, so fee/claim-dispute conversations (Intent.claim_dispute /
# Intent.fees_and_charges) have grounded, checkable expected outcomes.
# --------------------------------------------------------------------------- #
class FeeDispute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dispute_id: str
    description: str
    seed: int
    customer_id: str
    booking_id: str
    fee: FeeLine                       # the charge under dispute, as billed
    customer_message: str              # inbound "why was I charged X?"
    correct_amount: Decimal            # rule-correct amount (== fee.amount when upheld)
    resolution: DisputeResolution      # pinned expected outcome
    grounds: str                       # the policy/rule the resolution rests on


# --------------------------------------------------------------------------- #
# Scripted conversation trees (POA/16 §16.4 intents, §16.6 Phase-1 format)
#
# Deliberately SEPARATE from Scenario/Expected/TerminalState above: those model
# the *proactive* pipeline (did a trigger fire, was a claim verified). These
# model *inbound* conversations, which have different outcomes. Reusing the
# proactive enums here would force members onto TerminalState that M04/M07
# branch on — see POA/18 §4.
# --------------------------------------------------------------------------- #
class Intent(str, Enum):
    """The 17 conversation intents mandated by POA/16 §16.4."""
    new_booking = "new_booking"
    existing_booking_lookup = "existing_booking_lookup"
    booking_modification = "booking_modification"
    cancellation = "cancellation"
    pricing_quote = "pricing_quote"
    vehicle_availability = "vehicle_availability"
    vehicle_class_question = "vehicle_class_question"
    pickup_dropoff_question = "pickup_dropoff_question"
    fees_and_charges = "fees_and_charges"
    extras_insurance = "extras_insurance"
    payment_deposit = "payment_deposit"
    complaint = "complaint"
    claim_dispute = "claim_dispute"
    general_info = "general_info"
    out_of_scope = "out_of_scope"
    ambiguous = "ambiguous"
    requirements_change = "requirements_change"


class Speaker(str, Enum):
    customer = "customer"
    bot = "bot"


class ConversationOutcome(str, Enum):
    resolved = "resolved"                        # answered, nothing to change
    booking_created = "booking_created"
    booking_modified = "booking_modified"
    booking_cancelled = "booking_cancelled"
    escalated_to_human = "escalated_to_human"    # M07 handoff
    declined_out_of_scope = "declined_out_of_scope"
    abandoned = "abandoned"


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn: int
    speaker: Speaker
    text: str
    intent: Intent | None = None                 # customer turns only
    slots: dict[str, str] = Field(default_factory=dict)   # slots filled here
    claims: list[BookingClaim] = Field(default_factory=list)  # bot turns -> M10
    requests_handoff: bool = False


class ConversationExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: ConversationOutcome
    required_slots: list[str] = Field(default_factory=list)
    # every claim on a bot turn must have passed M10 before delivery
    all_claims_verified: bool = True

    # NOTE these two are different assertions — do not merge them.
    #
    # delivered_excludes: the token was WRONG against live data. M10 must
    #   correct or strip it, so it must never appear in ANY delivered turn.
    #   (Same meaning as Expected.delivered_excludes on the proactive side.)
    #
    # superseded_tokens: the token was CORRECT when said, and was legitimately
    #   delivered — then the customer changed a requirement. It must not appear
    #   in the FINAL confirmation or be carried into the booking. Asserting the
    #   stronger "never delivered" rule here would be wrong.
    delivered_excludes: list[str] = Field(default_factory=list)
    superseded_tokens: list[str] = Field(default_factory=list)
    min_bot_turns: int = 1


class ConversationBranch(BaseModel):
    """An alternate continuation taken from `from_turn` instead of the main path."""
    model_config = ConfigDict(extra="forbid")
    branch_id: str
    description: str
    from_turn: int
    turns: list[ConversationTurn]
    expected: ConversationExpected


class ConversationScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    intent: Intent
    description: str
    seed: int
    customer_id: str
    language: str = "en"
    turns: list[ConversationTurn]
    branches: list[ConversationBranch] = Field(default_factory=list)
    expected: ConversationExpected


# --------------------------------------------------------------------------- #
# PII redaction fixtures (POA/16 §16.5; M15 §4 is the source of truth)
#
# Every value in these fixtures is SYNTHETIC and drawn from a range that is
# reserved or invalid by specification, so a fixture can never collide with a
# real person's data. `generator/pii.py` builds them; `reference.redact()` is
# the executable spec of the redact-before-LLM decision (M08/M09).
# --------------------------------------------------------------------------- #
class PIIKind(str, Enum):
    full_name = "full_name"
    email = "email"
    phone = "phone"
    address = "address"
    driving_licence = "driving_licence"
    passport = "passport"
    date_of_birth = "date_of_birth"
    payment_card = "payment_card"
    booking_reference = "booking_reference"
    loyalty_number = "loyalty_number"
    vehicle_registration = "vehicle_registration"
    ip_address = "ip_address"


class PIICategory(str, Enum):
    """How the PII sits in the text — both are mandated by §16.5."""
    obvious = "obvious"      # a field dump / form-like paste
    embedded = "embedded"    # sitting naturally inside a conversational message


class PIISpan(BaseModel):
    """An exact, offset-addressed occurrence of PII.

    Offsets, not regexes: `text[start:end] == value` is asserted at build time,
    so redaction is deterministic and never has to guess — the same discipline
    as `BookingClaim.text_token`.
    """
    model_config = ConfigDict(extra="forbid")
    kind: PIIKind
    start: int
    end: int
    value: str


class RedactionFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture_id: str
    category: PIICategory
    description: str
    text: str                                    # the raw text, PII intact
    spans: list[PIISpan]
    redacted: str                                # expected output of redact()
    # non-PII substrings that MUST survive redaction — guards over-redaction,
    # which otherwise passes every "no PII remains" check trivially.
    preserves: list[str] = Field(default_factory=list)


class Dataset(BaseModel):
    """The full generated bundle."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    seed: int
    locations: list[Location]
    vehicle_classes: list[VehicleClass]
    rate_cards: list[RateCard]
    availability: list[Availability]
    customers: list[Customer]
    bookings: list[Booking]
    events: list[Event]
    triggers: list[TriggerConfig]
    routing_rules: list[RoutingRule]
    scenarios: list[Scenario] = Field(default_factory=list)
    # v0.2 — business layer + catalogues
    companies: list[Company] = Field(default_factory=list)
    rate_plans: list[RatePlan] = Field(default_factory=list)
    invoices: list[Invoice] = Field(default_factory=list)
    protection_products: list[ProtectionProduct] = Field(default_factory=list)
    extras: list[Extra] = Field(default_factory=list)
    policies: list[Policy] = Field(default_factory=list)
    # S3 — scripted conversation trees (POA/16 §16.4/§16.6)
    conversations: list[ConversationScenario] = Field(default_factory=list)
    # S4 — PII redaction fixtures (POA/16 §16.5)
    redaction_fixtures: list[RedactionFixture] = Field(default_factory=list)
    # S2 — hand-authored fee-dispute fixtures (POA/16 §16.1)
    fee_disputes: list[FeeDispute] = Field(default_factory=list)
    # S5 — load/SLA/timeout targets this dataset is sized against (POA/16 §16.3)
    load_profile: dict = Field(default_factory=dict)
