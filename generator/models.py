"""Pydantic contract models — the single source of truth (design principle P3).

The real services (M02 ingestion, M10 verification, M13 config …) should import
these same models, so any data the generator emits is schema-valid by
construction and contract tests pass for free.

Money is modelled with Decimal (never float) to match a real booking system.
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


class VehicleClass(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    label: str
    example_model: str


class RateCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str
    vehicle_class: str
    date: date
    daily_rate: Decimal
    currency: str = "GBP"


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
    negotiated_rate_plan: str | None = None
    last_booking_at: datetime | None = None


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
    status: str = "completed"


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
