"""Closing POA/09 §5 (1, 6) and POA/10 §5 (3, 4) to 7/7.

Covers:
  * the real Anthropic adapter — request shape, structured claim output, error
    mapping, refusal handling;
  * cost/latency budgets;
  * the HTTP booking-API client + auth;
  * the price tolerance policy.

Every test drives a **stub transport or stub SDK client** — no network, no
sleeping, no API key. That is deliberate: these are adapters, and an adapter
test that needs a live dependency is an integration test wearing the wrong hat.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from generator.models import ClaimKind
from services.conversation.claim_verification import (
    APIKeyAuth,
    BearerAuth,
    BookingAPIConfig,
    BookingAPIEndpoints,
    HMACAuth,
    HTTPBookingAPIClient,
    HTTPResponse,
    NoAuth,
    ToleranceMode,
    TolerancePolicy,
    auth_from_env,
)
from services.conversation.claim_verification.client import (
    BookingAPITimeout,
    BookingAPIUnavailable,
    NoDataForKey,
)
from services.conversation.claim_verification.tolerance import DEFAULT_POLICY, FROM_PRICE_POLICY
from services.conversation.llm import (
    CLAIM_SCHEMA,
    DEFAULT_MODEL,
    AnthropicConfig,
    AnthropicProvider,
    BudgetGuard,
    BudgetPolicy,
    BudgetVerdict,
    InMemoryBudgetStore,
    build_llm_response,
    parse_structured_output,
)
from services.conversation.llm.provider import ProviderTimeout, ProviderUnavailable

_TZ = timezone.utc
WHEN = datetime(2026, 9, 1, 10, 0, tzinfo=_TZ)
CTX = dict(
    pickup="LHR", dropoff="LHR", pickup_at=WHEN,
    return_at=WHEN + timedelta(days=3), vehicle_class="ICAR",
)


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# =========================================================================== #
# POA/09 §5.1 — the Anthropic adapter
# =========================================================================== #
class StubBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class StubUsage:
    def __init__(self, i: int = 120, o: int = 40) -> None:
        self.input_tokens = i
        self.output_tokens = o


class StubResponse:
    def __init__(self, payload, stop_reason="end_turn", stop_details=None) -> None:
        body = payload if isinstance(payload, str) else json.dumps(payload)
        self.content = [StubBlock(body)]
        self.stop_reason = stop_reason
        self.stop_details = stop_details
        self.usage = StubUsage()


class StubMessages:
    def __init__(self, response=None, raises=None) -> None:
        self.response = response
        self.raises = raises
        self.last_request: dict | None = None

    def create(self, **kwargs):
        self.last_request = kwargs
        if self.raises:
            raise self.raises
        return self.response


class StubClient:
    def __init__(self, response=None, raises=None) -> None:
        self.messages = StubMessages(response, raises)
        self.last_timeout = None

    def with_options(self, **kwargs):
        self.last_timeout = kwargs.get("timeout")
        return self


def provider(response=None, raises=None, config=None):
    client = StubClient(response, raises)
    return AnthropicProvider(client=client, config=config or AnthropicConfig()), client


def test_request_uses_the_current_model_and_structured_output():
    p, client = provider(StubResponse({"text": "Shall I help?", "confidence": 0.9, "claims": []}))
    p.generate("prompt", timeout=2.0)
    req = client.messages.last_request

    assert req["model"] == DEFAULT_MODEL == "claude-opus-5"
    assert req["output_config"]["format"] is CLAIM_SCHEMA
    assert req["output_config"]["effort"] == "low", "generation is inline before delivery"
    assert client.last_timeout == 2.0
    assert "thinking" not in req, "thinking is adaptive by default on Opus 5; do not disable it"
    assert "budget_tokens" not in json.dumps(req), "budget_tokens is rejected on this model"


def test_system_prompt_is_cached_and_forbids_inventing_prices():
    p, client = provider(StubResponse({"text": "ok booking", "confidence": 0.9, "claims": []}))
    p.generate("prompt", timeout=2.0)
    system = client.messages.last_request["system"]

    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "Never invent a price" in system[0]["text"]
    assert "card details" in system[0]["text"], "must not solicit PII in chat"


def test_inference_geo_is_a_top_level_parameter_when_configured():
    p, client = provider(
        StubResponse({"text": "booking", "confidence": 0.9, "claims": []}),
        config=AnthropicConfig(inference_geo="eu"),
    )
    p.generate("p", timeout=1.0)
    assert client.messages.last_request["inference_geo"] == "eu"


@pytest.mark.parametrize(
    "exc_name, expected",
    [
        ("APITimeoutError", ProviderTimeout),
        ("APIConnectionError", ProviderUnavailable),
        ("RateLimitError", ProviderUnavailable),
    ],
)
def test_sdk_errors_map_onto_the_provider_contract(exc_name, expected):
    """Whatever goes wrong, M09's fallback path handles it — nothing leaks out."""
    import anthropic

    exc_cls = getattr(anthropic, exc_name)
    try:
        exc = exc_cls("boom", request=None, body=None)      # type: ignore[call-arg]
    except TypeError:
        exc = exc_cls.__new__(exc_cls)
        Exception.__init__(exc, "boom")

    p, _ = provider(raises=exc)
    with pytest.raises(expected):
        p.generate("p", timeout=1.0)


def test_a_refusal_is_flagged_not_raised():
    """A refusal is HTTP 200 with stop_reason 'refusal'. It must reach the
    confidence gate as a safety flag, which turns it into a templated fallback."""
    class Details:
        category = "cyber"

    p, _ = provider(StubResponse(
        {"text": "", "confidence": 0.0, "claims": []},
        stop_reason="refusal", stop_details=Details(),
    ))
    result = p.generate("p", timeout=1.0)
    assert result.safety_flags == ["refusal:cyber"]


def test_usage_is_captured_for_the_budget_guard():
    p, _ = provider(StubResponse({"text": "booking", "confidence": 0.9, "claims": []}))
    result = p.generate("p", timeout=1.0)
    assert result.usage.prompt_tokens == 120 and result.usage.completion_tokens == 40


# ---- structured claim output: the M09 -> M10 contract ---------------------- #
def test_structured_claims_become_booking_claims():
    payload = {
        "text": "Your Intermediate is £48.50/day — shall I hold it?",
        "confidence": 0.88,
        "claims": [{"kind": "price", "text_token": "£48.50", "quoted_price": "48.50"}],
    }
    response = build_llm_response(payload, **CTX)
    assert response.confidence == 0.88
    assert len(response.claims) == 1
    claim = response.claims[0]
    assert claim.kind is ClaimKind.price
    assert claim.quoted_price == Decimal("48.50")
    assert claim.text_token in response.text


def test_a_claim_whose_token_is_not_in_the_text_is_dropped():
    """An unverifiable tag is worse than no tag — it looks like coverage while
    M10 has nothing it can address."""
    payload = {
        "text": "Shall I help you finish the booking?",
        "confidence": 0.9,
        "claims": [{"kind": "price", "text_token": "£99.99", "quoted_price": "99.99"}],
    }
    assert build_llm_response(payload, **CTX).claims == []


def test_claims_are_dropped_when_route_context_is_missing():
    """Without route and dates the booking API cannot be queried, so the claim
    cannot be verified — M10's pattern fallback gets a chance instead."""
    payload = {
        "text": "It's £48.50/day.",
        "confidence": 0.9,
        "claims": [{"kind": "price", "text_token": "£48.50", "quoted_price": "48.50"}],
    }
    assert build_llm_response(payload).claims == []


def test_malformed_model_output_degrades_instead_of_raising():
    """POA/09 §1: an error never reaches the customer. Unparseable output yields
    an empty draft, which the confidence gate turns into a safe fallback."""
    payload = parse_structured_output("not json at all")
    assert payload == {"text": "", "confidence": 0.0, "claims": []}
    assert build_llm_response(payload).text == ""


def test_unknown_claim_kind_is_dropped():
    payload = {
        "text": "It's £48.50/day.", "confidence": 0.9,
        "claims": [{"kind": "vibes", "text_token": "£48.50", "quoted_price": "48.50"}],
    }
    assert build_llm_response(payload, **CTX).claims == []


# =========================================================================== #
# POA/09 §5.6 — cost & latency budgets
# =========================================================================== #
def guard(clock=None, **policy):
    clock = clock or FakeClock()
    return BudgetGuard(BudgetPolicy(**policy), InMemoryBudgetStore(clock=clock), clock=clock)


def test_budget_allows_normal_traffic():
    assert guard().check("s1", "c1").allowed


def test_session_token_budget_stops_a_runaway_conversation():
    g = guard(max_tokens_per_session=1000)
    g.record("s1", "c1", 600, 500)
    decision = g.check("s1", "c1")
    assert not decision.allowed
    assert decision.verdict is BudgetVerdict.session_tokens_exceeded
    assert g.check("s2", "c1").allowed, "a different session must be unaffected"


def test_customer_daily_budget_is_independent_of_session():
    g = guard(max_tokens_per_session=0, max_tokens_per_customer_per_day=1000)
    g.record("s1", "c1", 600, 500)
    assert g.check("s2", "c1").verdict is BudgetVerdict.customer_tokens_exceeded
    assert g.check("s2", "c2").allowed


def test_global_spend_budget_is_money_not_tokens():
    g = guard(max_tokens_per_session=0, max_tokens_per_customer_per_day=0,
              max_spend_per_day=Decimal("0.01"))
    g.record("s1", "c1", 1_000_000, 1_000_000)      # $5 + $25
    decision = g.check("s2", "c2")
    assert decision.verdict is BudgetVerdict.global_spend_exceeded
    assert decision.daily_spend == Decimal("30.000000")


def test_cost_uses_configured_prices_not_hard_coded_ones():
    """Published prices change; a stale constant silently under-reports spend."""
    policy = BudgetPolicy(input_price_per_mtok=Decimal("2.00"),
                          output_price_per_mtok=Decimal("10.00"))
    assert policy.cost(1_000_000, 1_000_000) == Decimal("12.000000")


def test_budgets_expire_on_the_injected_clock():
    clock = FakeClock()
    g = guard(clock=clock, max_tokens_per_session=1000)
    g.record("s1", "c1", 600, 500)
    assert not g.check("s1", "c1").allowed
    clock.advance(4 * 60 * 60 + 1)                  # past the session window
    assert g.check("s1", "c1").allowed


def test_check_happens_before_spend_not_after():
    """Recording alone would let one very large request blow past the cap."""
    g = guard(max_tokens_per_session=1000)
    assert g.check("s1", "c1").allowed
    g.record("s1", "c1", 5000, 5000)
    assert not g.check("s1", "c1").allowed


def test_zero_disables_a_limit():
    g = guard(max_tokens_per_session=0)
    g.record("s1", "c1", 10_000_000, 10_000_000)
    assert g.check("s1", "c1").verdict is not BudgetVerdict.session_tokens_exceeded


# =========================================================================== #
# POA/10 §5.4 — tolerance policy
# =========================================================================== #
@pytest.mark.parametrize(
    "mode, kwargs, quoted, actual, expected",
    [
        (ToleranceMode.exact, {}, "48.50", "48.50", True),
        (ToleranceMode.exact, {}, "48.50", "48.51", False),
        (ToleranceMode.absolute, {"absolute": Decimal("0.01")}, "48.50", "48.51", True),
        (ToleranceMode.absolute, {"absolute": Decimal("0.01")}, "48.50", "48.75", False),
        (ToleranceMode.percentage, {"percentage": Decimal("0.05")}, "48.00", "50.00", True),
        (ToleranceMode.percentage, {"percentage": Decimal("0.01")}, "48.00", "50.00", False),
        (ToleranceMode.rounded, {"round_to": Decimal("1.00")}, "49", "48.50", True),
        (ToleranceMode.rounded, {"round_to": Decimal("1.00")}, "47", "48.50", False),
    ],
)
def test_tolerance_modes(mode, kwargs, quoted, actual, expected):
    policy = TolerancePolicy(mode=mode, **kwargs)
    assert policy.accepts(Decimal(quoted), Decimal(actual)) is expected


def test_from_price_semantics_are_the_right_way_round():
    """"From £42" promises the customer can get it FOR £42. It holds when the
    live price is at or below that, and fails when the real price is higher —
    a customer quoted "from £42" and charged £55 was misled."""
    assert FROM_PRICE_POLICY.accepts(Decimal("42.00"), Decimal("38.00")) is True
    assert FROM_PRICE_POLICY.accepts(Decimal("42.00"), Decimal("42.00")) is True
    assert FROM_PRICE_POLICY.accepts(Decimal("42.00"), Decimal("55.00")) is False


def test_default_policy_is_strict():
    """A tolerance that is too loose lets a wrong price reach a customer."""
    assert DEFAULT_POLICY.mode is ToleranceMode.absolute
    assert DEFAULT_POLICY.absolute == Decimal("0.01")
    assert not DEFAULT_POLICY.accepts(Decimal("48.00"), Decimal("48.50"))


def test_policy_describes_itself_for_the_audit_log():
    assert "0.01" in DEFAULT_POLICY.describe()
    assert "floor" in FROM_PRICE_POLICY.describe()


def test_service_records_the_rule_alongside_the_outcome(world):
    """A verification outcome is only meaningful next to the rule that made it."""
    from mocks.booking_api import BookingAPIMock
    from services.conversation.claim_verification import (
        ClaimVerificationService, MockClientAdapter,
    )
    from generator.models import BookingClaim

    rate = world.rate("LHR", "ICAR", world.start)
    when = datetime(world.start.year, world.start.month, world.start.day, 10, 0, tzinfo=_TZ)
    svc = ClaimVerificationService(MockClientAdapter(BookingAPIMock(world)))
    claim = BookingClaim(
        kind=ClaimKind.price, pickup="LHR", dropoff="LHR", pickup_at=when,
        return_at=when + timedelta(days=3), vehicle_class="ICAR",
        quoted_price=rate, text_token=f"£{rate:.2f}",
    )
    out = svc.verify_response(f"It's £{rate:.2f}.", [claim])
    assert out.records[0].tolerance_rule == DEFAULT_POLICY.describe()


def test_at_least_policy_accepts_a_cheaper_live_price_through_the_service(world):
    from mocks.booking_api import BookingAPIMock
    from services.conversation.claim_verification import (
        ClaimVerificationService, MockClientAdapter,
    )
    from generator.models import BookingClaim, VerifyStatus

    rate = world.rate("LHR", "ICAR", world.start)
    quoted = rate + Decimal("10.00")                      # "from £X" above the real price
    when = datetime(world.start.year, world.start.month, world.start.day, 10, 0, tzinfo=_TZ)
    svc = ClaimVerificationService(
        MockClientAdapter(BookingAPIMock(world)), tolerance_policy=FROM_PRICE_POLICY
    )
    claim = BookingClaim(
        kind=ClaimKind.price, pickup="LHR", dropoff="LHR", pickup_at=when,
        return_at=when + timedelta(days=3), vehicle_class="ICAR",
        quoted_price=quoted, text_token=f"from £{quoted:.2f}",
    )
    out = svc.verify_response(f"Rentals from £{quoted:.2f}.", [claim])
    assert out.records[0].status is VerifyStatus.ok


# =========================================================================== #
# POA/10 §5.3 — HTTP client + auth
# =========================================================================== #
class StubTransport:
    def __init__(self, *responses, raises=None) -> None:
        self.responses = list(responses)
        self.raises = raises
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        if self.raises:
            raise self.raises
        return self.responses.pop(0) if self.responses else HTTPResponse(200, "{}")


def http(*responses, auth=None, raises=None, config=None):
    transport = StubTransport(*responses, raises=raises)
    client = HTTPBookingAPIClient(
        config=config or BookingAPIConfig(base_url="https://api.example.invalid"),
        auth=auth or NoAuth(),
        transport=transport,
    )
    return client, transport


def test_rate_lookup_builds_the_expected_request():
    client, transport = http(HTTPResponse(200, json.dumps({"daily_rate": "48.50"})))
    assert client.rate("LHR", "ICAR", date(2026, 9, 1)) == Decimal("48.50")

    url, headers, timeout = transport.calls[0]
    assert url.startswith("https://api.example.invalid/v1/rates?")
    assert "location=LHR" in url and "vehicle_class=ICAR" in url and "date=2026-09-01" in url
    assert headers["Accept"] == "application/json"
    assert timeout == 1.0


def test_availability_lookup():
    client, _ = http(HTTPResponse(200, json.dumps({"available": 7})))
    assert client.availability("LHR", "ICAR", date(2026, 9, 1)) == 7


def test_endpoint_shape_is_overridable_without_touching_the_client():
    """POA/10 §10.1 is open — adapting to the real contract must be config."""
    config = BookingAPIConfig(
        base_url="https://real.example.invalid",
        endpoints=BookingAPIEndpoints(
            rate_path="/pricing/daily", location_param="stn",
            vehicle_class_param="cls", date_param="d", rate_field="netRate",
        ),
    )
    client, transport = http(HTTPResponse(200, json.dumps({"netRate": "51.00"})), config=config)
    assert client.rate("MAN", "ECAR", date(2026, 9, 2)) == Decimal("51.00")
    url = transport.calls[0][0]
    assert "/pricing/daily?" in url and "stn=MAN" in url and "cls=ECAR" in url


@pytest.mark.parametrize(
    "status, expected",
    [
        (404, NoDataForKey),
        (401, BookingAPIUnavailable),
        (403, BookingAPIUnavailable),
        (429, BookingAPIUnavailable),
        (500, BookingAPIUnavailable),
        (503, BookingAPIUnavailable),
    ],
)
def test_http_statuses_map_onto_the_contract_m10_already_handles(status, expected):
    client, _ = http(HTTPResponse(status, "{}"))
    with pytest.raises(expected):
        client.rate("LHR", "ICAR", date(2026, 9, 1))


def test_timeout_propagates_as_a_booking_api_timeout():
    client, _ = http(raises=BookingAPITimeout("too slow"))
    with pytest.raises(BookingAPITimeout):
        client.rate("LHR", "ICAR", date(2026, 9, 1))


def test_malformed_json_is_an_outage_not_a_bad_lookup():
    """A 200 we cannot read means the dependency is broken — it should count
    towards the circuit breaker, unlike a 404."""
    client, _ = http(HTTPResponse(200, "<html>oops</html>"))
    with pytest.raises(BookingAPIUnavailable):
        client.rate("LHR", "ICAR", date(2026, 9, 1))


def test_missing_field_is_no_data_not_an_outage():
    client, _ = http(HTTPResponse(200, json.dumps({"something_else": 1})))
    with pytest.raises(NoDataForKey):
        client.rate("LHR", "ICAR", date(2026, 9, 1))


def test_the_http_client_satisfies_the_protocol_m10_expects():
    from services.conversation.claim_verification.client import BookingAPIClient

    client, _ = http()
    assert isinstance(client, BookingAPIClient)


# ---- auth ------------------------------------------------------------------ #
def test_bearer_auth_sets_the_header():
    client, transport = http(HTTPResponse(200, json.dumps({"daily_rate": "1"})),
                             auth=BearerAuth("tok-123"))
    client.rate("LHR", "ICAR", date(2026, 9, 1))
    assert transport.calls[0][1]["Authorization"] == "Bearer tok-123"


def test_api_key_auth_uses_the_configured_header_name():
    client, transport = http(HTTPResponse(200, json.dumps({"daily_rate": "1"})),
                             auth=APIKeyAuth("k", header_name="X-Hertz-Key"))
    client.rate("LHR", "ICAR", date(2026, 9, 1))
    assert transport.calls[0][1]["X-Hertz-Key"] == "k"


def test_hmac_auth_signs_deterministically():
    auth = HMACAuth("kid", "s3cret", clock=lambda: 1_700_000_000)
    first = auth.headers("GET", "/v1/rates")
    second = auth.headers("GET", "/v1/rates")
    assert first == second
    assert first["X-Key-Id"] == "kid" and len(first["X-Signature"]) == 64
    assert auth.headers("GET", "/v1/availability")["X-Signature"] != first["X-Signature"]


def test_credentials_never_appear_in_a_repr():
    """A config in a stack trace must not print the secret."""
    for auth in (BearerAuth("supersecret"), APIKeyAuth("supersecret"),
                 HMACAuth("kid", "supersecret")):
        assert "supersecret" not in repr(auth)
        assert "***" in repr(auth)


def test_auth_is_read_from_the_environment_never_hard_coded(monkeypatch):
    monkeypatch.delenv("BOOKING_API_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("BOOKING_API_KEY", raising=False)
    monkeypatch.delenv("BOOKING_API_HMAC_KEY_ID", raising=False)
    monkeypatch.delenv("BOOKING_API_HMAC_SECRET", raising=False)
    assert isinstance(auth_from_env(), NoAuth)

    monkeypatch.setenv("BOOKING_API_BEARER_TOKEN", "t")
    assert isinstance(auth_from_env(), BearerAuth)

    monkeypatch.delenv("BOOKING_API_BEARER_TOKEN")
    monkeypatch.setenv("BOOKING_API_HMAC_KEY_ID", "kid")
    monkeypatch.setenv("BOOKING_API_HMAC_SECRET", "sec")
    assert isinstance(auth_from_env(), HMACAuth)
