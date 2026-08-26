"""ScenarioComposer — the golden/fixture tier (Tier A).

~7 hand-composed, deterministic scenarios with pinned expected outcomes, one per
distinct decision branch (POA/16 §5). All claims are grounded in the SAME world
the booking-API mock reads, so verification tests are airtight.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .models import (
    BookingClaim,
    BookingStep,
    ClaimKind,
    Consent,
    Customer,
    CustomerType,
    Event,
    EventContext,
    Expected,
    FailureKey,
    FrequencyCap,
    LLMResponse,
    MessageKind,
    Scenario,
    Segment,
    SignalType,
    TerminalState,
    TriggerConfig,
    TriggerMatch,
    TriggerType,
)
from .world import World

_TZ = timezone.utc


def _customer(cid: str, language: str = "en", ctype: CustomerType = CustomerType.SME) -> Customer:
    region = {"en": "UK", "de": "DE", "fr": "FR", "es": "ES"}[language]
    return Customer(
        customer_id=cid,
        customer_type=ctype,
        region=region,
        language=language,
        segment=Segment.frequent,
        created_at=datetime(2025, 1, 1, tzinfo=_TZ),
        consent=Consent(),
        negotiated_rate_plan=f"{ctype.value}-STD-2026" if ctype != CustomerType.individual else None,
    )


class ScenarioComposer:
    def __init__(self, world: World) -> None:
        self.world = world
        self.day = world.start
        self.pickup_at = datetime(self.day.year, self.day.month, self.day.day, 10, 0, tzinfo=_TZ)
        self.return_at = self.pickup_at + timedelta(days=3)

    # helpers ------------------------------------------------------------ #
    def _event(self, cid: str, signal: SignalType, loc: str, vc: str, **ctx) -> Event:
        base = dict(
            pickup=loc, dropoff=loc, pickup_at=self.pickup_at, return_at=self.return_at, vehicle_class=vc
        )
        base.update(ctx)
        return Event(
            event_id=str(uuid.uuid4()),
            customer_id=cid,
            session_id=f"sess-{cid[-4:]}-00",
            signal_type=signal,
            occurred_at=self.pickup_at + timedelta(minutes=5),
            context=EventContext(**base),
        )

    def _trigger(self, signal: SignalType, cap_max: int = 1) -> TriggerConfig:
        return TriggerConfig(
            trigger_id=f"{signal.value}_v1",
            match=TriggerMatch(signal_type=signal),
            type=TriggerType.in_session,
            frequency_cap=FrequencyCap(per="P7D", max=cap_max),
            message_template_ref=f"tmpl_{signal.value}",
        )

    def _price_claim(self, loc: str, vc: str, quoted: Decimal, token: str) -> BookingClaim:
        return BookingClaim(
            kind=ClaimKind.price,
            pickup=loc,
            dropoff=loc,
            pickup_at=self.pickup_at,
            return_at=self.return_at,
            vehicle_class=vc,
            quoted_price=quoted,
            text_token=token,
        )

    def _find_zero_availability(self) -> tuple[str, str]:
        """A (location, class) whose availability on world.start is 0."""
        for a in self.world.availability:
            if a.date == self.day and a.available == 0:
                return a.location_id, a.vehicle_class
        # fallback: any key (should not happen with the seeded world)
        return "LHR", "SUV"

    # scenarios ---------------------------------------------------------- #
    def all(self) -> list[Scenario]:
        return [
            self.gs01(),
            self.gs02(),
            self.gs03(),
            self.gs04(),
            self.gs05(),
            self.gs06(),
            self.gs07(),
        ]

    def gs01(self) -> Scenario:
        """search_no_convert -> fire -> LLM no claim -> responds+resolves -> converted."""
        cid = "hfb-cust-gs01"
        loc, vc = "LHR", "ICAR"
        return Scenario(
            scenario_id="GS-01-search-no-claim-convert",
            description="In-session fire, plain LLM reply (no factual claim), customer resolves and books.",
            seed=101,
            customer=_customer(cid),
            trigger=self._trigger(SignalType.search_no_convert),
            events=[self._event(cid, SignalType.search_no_convert, loc, vc)],
            llm_response=LLMResponse(
                text="Looks like you were comparing options at London Heathrow — want a hand narrowing it down?",
                confidence=0.9,
            ),
            replies=["yes please"],
            resolves=True,
            expected=Expected(
                fired=True,
                message_kind=MessageKind.llm,
                terminal_state=TerminalState.converted,
            ),
        )

    def gs02(self) -> Scenario:
        """booking_abandoned -> fire -> CORRECT price claim -> verified -> no response."""
        cid = "hfb-cust-gs02"
        loc, vc = "LHR", "ICAR"
        rate = self.world.rate(loc, vc, self.day)
        token = f"£{rate:.2f}"
        return Scenario(
            scenario_id="GS-02-price-correct-verified",
            description="LLM quotes the true live price; verification passes it through unchanged.",
            seed=102,
            customer=_customer(cid),
            trigger=self._trigger(SignalType.booking_abandoned),
            events=[self._event(cid, SignalType.booking_abandoned, loc, vc, step=BookingStep.payment)],
            llm_response=LLMResponse(
                text=f"Your Intermediate at London Heathrow is {token}/day — shall I hold it while you finish?",
                claims=[self._price_claim(loc, vc, rate, token)],
                confidence=0.88,
            ),
            replies=[],
            resolves=None,
            expected=Expected(
                fired=True,
                message_kind=MessageKind.verified,
                terminal_state=TerminalState.no_engagement,
            ),
        )

    def gs03(self) -> Scenario:
        """rate_view_no_progress -> WRONG price -> corrected -> responds, not resolved -> handoff."""
        cid = "hfb-cust-gs03"
        loc, vc = "LHR", "ICAR"
        rate = self.world.rate(loc, vc, self.day)
        wrong = (rate - Decimal("10.00")).quantize(Decimal("0.01"))
        wrong_token = f"£{wrong:.2f}"
        return Scenario(
            scenario_id="GS-03-price-wrong-corrected-handoff",
            description="LLM under-quotes; verifier corrects to live price; bot can't resolve -> handoff.",
            seed=103,
            customer=_customer(cid),
            trigger=self._trigger(SignalType.rate_view_no_progress),
            events=[self._event(cid, SignalType.rate_view_no_progress, loc, vc)],
            llm_response=LLMResponse(
                text=f"Good news — I can do that Intermediate for just {wrong_token}/day!",
                claims=[self._price_claim(loc, vc, wrong, wrong_token)],
                confidence=0.8,
            ),
            replies=["that's not what I saw", "this is wrong"],
            resolves=False,
            expected=Expected(
                fired=True,
                message_kind=MessageKind.corrected,
                terminal_state=TerminalState.handed_off,
                delivered_excludes=[wrong_token],  # the wrong price must never survive
            ),
        )

    def gs04(self) -> Scenario:
        """error_hit -> price claim but booking API times out -> UNVERIFIABLE -> stripped."""
        cid = "hfb-cust-gs04"
        loc, vc = "LHR", "ICAR"
        rate = self.world.rate(loc, vc, self.day)
        token = f"£{rate:.2f}"
        return Scenario(
            scenario_id="GS-04-price-unverifiable-stripped",
            description="Booking API unavailable during verification -> claim stripped, safe message still sent.",
            seed=104,
            customer=_customer(cid),
            trigger=self._trigger(SignalType.error_hit),
            events=[self._event(cid, SignalType.error_hit, loc, vc, error_code="PAYMENT_DECLINED")],
            llm_response=LLMResponse(
                text=f"No problem — that car is still {token}/day, want to try again?",
                claims=[self._price_claim(loc, vc, rate, token)],
                confidence=0.85,
            ),
            booking_api_failures=[FailureKey(location_id=loc, vehicle_class=vc, date=self.day)],
            replies=[],
            resolves=None,
            expected=Expected(
                fired=True,
                message_kind=MessageKind.stripped,
                terminal_state=TerminalState.no_engagement,
                delivered_excludes=[token],  # even a correct-looking price must be stripped
            ),
        )

    def gs05(self) -> Scenario:
        """search_no_convert -> frequency cap already hit -> SUPPRESSED (Z1)."""
        cid = "hfb-cust-gs05"
        loc, vc = "MAN", "ECAR"
        ev = self._event(cid, SignalType.search_no_convert, loc, vc)
        return Scenario(
            scenario_id="GS-05-frequency-cap-suppressed",
            description="Customer engaged 1 day ago; cap is 1 per 7 days -> this match is suppressed.",
            seed=105,
            customer=_customer(cid),
            trigger=self._trigger(SignalType.search_no_convert, cap_max=1),
            events=[ev],
            prior_engagements=[ev.occurred_at - timedelta(days=1)],
            expected=Expected(fired=False, suppressed_reason="frequency_cap"),
        )

    def gs06(self) -> Scenario:
        """extended_dwell -> LLM timeout -> localised fallback (German) -> converted."""
        cid = "hfb-cust-gs06"
        loc, vc = "FRA", "FCAR"
        return Scenario(
            scenario_id="GS-06-llm-timeout-fallback-de",
            description="LLM provider times out; safe localised (de) fallback is delivered instead.",
            seed=106,
            customer=_customer(cid, language="de", ctype=CustomerType.corporate),
            trigger=self._trigger(SignalType.extended_dwell),
            events=[self._event(cid, SignalType.extended_dwell, loc, vc, step=BookingStep.select_vehicle, dwell_ms=95000)],
            llm_response=None,
            llm_timeout=True,
            replies=["ja, gerne"],
            resolves=True,
            expected=Expected(
                fired=True,
                message_kind=MessageKind.fallback,
                terminal_state=TerminalState.converted,
            ),
        )

    def gs07(self) -> Scenario:
        """booking_abandoned -> availability claim wrong (actual 0) -> corrected -> handoff."""
        cid = "hfb-cust-gs07"
        loc, vc = self._find_zero_availability()
        token = "available now"
        claim = BookingClaim(
            kind=ClaimKind.availability,
            pickup=loc,
            dropoff=loc,
            pickup_at=self.pickup_at,
            return_at=self.return_at,
            vehicle_class=vc,
            quoted_available=True,
            text_token=token,
        )
        return Scenario(
            scenario_id="GS-07-availability-wrong-corrected",
            description="LLM claims the car is available but the world says 0 -> claim corrected.",
            seed=107,
            customer=_customer(cid),
            trigger=self._trigger(SignalType.booking_abandoned),
            events=[self._event(cid, SignalType.booking_abandoned, loc, vc, step=BookingStep.review)],
            llm_response=LLMResponse(
                text=f"That vehicle is {token} at your pickup location — shall I reserve it?",
                claims=[claim],
                confidence=0.83,
            ),
            replies=["hmm not sure"],
            resolves=False,
            expected=Expected(
                fired=True,
                message_kind=MessageKind.corrected,
                terminal_state=TerminalState.handed_off,
                delivered_excludes=[token],
            ),
        )
