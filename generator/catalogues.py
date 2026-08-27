"""Static reference catalogues (v0.2): protection products, extras, policies and
negotiated rate plans.

These are small, hand-authored, business-realistic reference sets (Hertz UK
conventions) — not seeded/random. They give the conversational chatbot concrete
data to answer insurance, extras, policy and "what's my company rate?" queries.
Terminology and defaults are placeholders to be swapped for the client's own.
"""
from __future__ import annotations

from decimal import Decimal

from .models import (
    CustomerType,
    Extra,
    Policy,
    PolicyTopic,
    PricingUnit,
    ProtectionProduct,
    RatePlan,
)


# --------------------------------------------------------------------------- #
# Protection / insurance products (M10 verifiable "included cover" queries)
# --------------------------------------------------------------------------- #
def protection_products() -> list[ProtectionProduct]:
    return [
        ProtectionProduct(
            product_id="prot-cdw", code="CDW", name="Collision Damage Waiver",
            daily_price=Decimal("0.00"), excess_before=Decimal("1500.00"),
            excess_after=Decimal("1500.00"), included_by_default=True,
            description="Included. Caps your liability for accidental damage at the standard excess.",
        ),
        ProtectionProduct(
            product_id="prot-tp", code="TP", name="Theft Protection",
            daily_price=Decimal("0.00"), excess_before=Decimal("1500.00"),
            excess_after=Decimal("1500.00"), included_by_default=True,
            description="Included. Caps your liability if the vehicle is stolen.",
        ),
        ProtectionProduct(
            product_id="prot-super", code="SUPERCOVER", name="Super Cover (Excess Reduction)",
            daily_price=Decimal("15.99"), excess_before=Decimal("1500.00"),
            excess_after=Decimal("0.00"),
            description="Reduces the damage & theft excess to zero for the rental.",
        ),
        ProtectionProduct(
            product_id="prot-pai", code="PAI", name="Personal Accident Insurance",
            daily_price=Decimal("5.49"),
            description="Accident cover for the driver and passengers.",
        ),
        ProtectionProduct(
            product_id="prot-tws", code="TWS", name="Tyre & Windscreen Protection",
            daily_price=Decimal("6.99"),
            description="Removes liability for tyre and windscreen damage.",
        ),
    ]


# --------------------------------------------------------------------------- #
# Extras / add-ons
# --------------------------------------------------------------------------- #
def extras() -> list[Extra]:
    return [
        Extra(extra_id="extra-adddriver", code="ADD_DRIVER", name="Additional Driver",
              pricing_unit=PricingUnit.per_day, price=Decimal("9.50"), max_qty=3,
              description="Add a second (or more) authorised driver."),
        Extra(extra_id="extra-gps", code="GPS", name="Sat Nav",
              pricing_unit=PricingUnit.per_day, price=Decimal("12.99"), max_qty=1,
              description="In-car satellite navigation."),
        Extra(extra_id="extra-childseat", code="CHILD_SEAT", name="Child Seat",
              pricing_unit=PricingUnit.per_day, price=Decimal("8.50"), max_qty=3,
              description="Infant, child or booster seat."),
        Extra(extra_id="extra-fpo", code="FPO", name="Fuel Purchase Option",
              pricing_unit=PricingUnit.per_rental, price=Decimal("75.00"), max_qty=1,
              description="Pre-pay for a full tank; return the vehicle empty."),
        Extra(extra_id="extra-winter", code="WINTER_TYRES", name="Winter Tyres",
              pricing_unit=PricingUnit.per_rental, price=Decimal("45.00"), max_qty=1,
              description="Winter tyres fitted for cold-weather rentals."),
    ]


# --------------------------------------------------------------------------- #
# Policies (queryable rental terms — the chatbot quotes `summary` verbatim)
# --------------------------------------------------------------------------- #
def policies() -> list[Policy]:
    return [
        Policy(policy_id="pol-mileage-uk", topic=PolicyTopic.mileage, applies_to="UK",
               summary="Unlimited mileage on standard UK car rentals.",
               detail="Vans and certain premium classes may carry a daily mileage cap; "
                      "cross-border rentals are limited — see the cross-border policy."),
        Policy(policy_id="pol-fuel", topic=PolicyTopic.fuel, applies_to="all",
               summary="Fuel policy is full-to-full: collect and return with a full tank.",
               detail="Alternatively pre-pay with the Fuel Purchase Option (FPO) and return empty. "
                      "A refuelling charge applies if returned below the pickup level."),
        Policy(policy_id="pol-deposit", topic=PolicyTopic.deposit, applies_to="all",
               summary="A refundable security deposit is pre-authorised on a credit card at pickup.",
               detail="The amount varies by vehicle class (from £150 for economy cars up to £500 for "
                      "vans) and is released after the vehicle is returned undamaged."),
        Policy(policy_id="pol-cancel", topic=PolicyTopic.cancellation, applies_to="all",
               summary="Free cancellation up to 48 hours before pickup.",
               detail="Cancellations inside 48 hours are charged one rental day. Prepaid rates may be "
                      "non-refundable — check the rate conditions at booking."),
        Policy(policy_id="pol-age", topic=PolicyTopic.driver_age, applies_to="all",
               summary="Minimum driver age is 21 for cars (25 for vans and premium classes).",
               detail="Drivers under 25 may incur a young-driver surcharge and some classes are "
                      "restricted. A full licence held for at least one year is required."),
        Policy(policy_id="pol-crossborder", topic=PolicyTopic.cross_border, applies_to="all",
               summary="Cross-border and one-way travel must be declared and may need prior approval.",
               detail="Some countries are excluded; a one-way fee and additional cover may apply."),
        Policy(policy_id="pol-late", topic=PolicyTopic.late_return, applies_to="all",
               summary="A 29-minute grace period applies; beyond it a further rental day may be charged.",
               detail="Please contact the branch if you expect to return late to avoid extra charges."),
    ]


# --------------------------------------------------------------------------- #
# Negotiated rate plans — makes `negotiated_rate_plan` resolvable to a real rate
# --------------------------------------------------------------------------- #
def rate_plans() -> list[RatePlan]:
    """One standard plan per business customer type. The id matches the existing
    `negotiated_rate_plan` convention (`<TYPE>-STD-2026`) so the string field now
    points at a real, priced plan."""
    return [
        RatePlan(
            rate_plan_id=f"{CustomerType.SME.value}-STD-2026",
            name="SME Standard 2026",
            discount_pct=Decimal("0.10"),
            included_extras=["ADD_DRIVER"],
        ),
        RatePlan(
            rate_plan_id=f"{CustomerType.corporate.value}-STD-2026",
            name="Corporate Standard 2026",
            discount_pct=Decimal("0.15"),
            included_protections=["CDW", "TP"],
            included_extras=["ADD_DRIVER", "GPS"],
        ),
    ]


def plan_for(customer_type: CustomerType) -> str | None:
    """The rate-plan id a business customer of this type is on (None for individuals)."""
    if customer_type == CustomerType.individual:
        return None
    return f"{customer_type.value}-STD-2026"
