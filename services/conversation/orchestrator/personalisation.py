"""Personalisation resolver (M08 node U) — POA/08 §3.2.

Maps (customer_type, region, language) onto tone, locale and template variant.

**The taxonomy is derived, not confirmed.** POA/08 §10.2 asks what the
personalisation dimensions actually are; nobody has answered. Rather than invent
one, this uses what already exists in the codebase — `CustomerType`, `Segment`,
`Customer.region`, and the four locales M09's fallback catalogue supports — so
the resolver is grounded in real data and product can correct it later. That is
recorded in POA/08 §11.

**The safe default is the point.** §8's third risk is "over-personalisation /
wrong language". Every unknown dimension resolves to a defined default rather
than guessing, and a language M09 cannot render a fallback in resolves to `en`
*and is flagged* — silently serving English is the failure mode, so it surfaces.
"""
from __future__ import annotations

from dataclasses import dataclass

from generator.models import CustomerType, Segment
from services.conversation.llm.fallback import DEFAULT_LOCALE, SUPPORTED_LOCALES


class Tone:
    """Tone labels. Strings, not an enum: product will rename these, and a
    rename should not be a migration."""
    EFFICIENT = "efficient"        # business accounts want the fastest path
    WARM = "warm"                  # individuals, and anyone we have not seen before
    DEFERENTIAL = "deferential"    # large corporate accounts


@dataclass(frozen=True)
class Personalisation:
    tone: str
    locale: str
    formality: str                 # "formal" | "neutral"
    template_variant: str
    locale_missing: bool = False   # asked for a language M09 cannot render

    def as_dict(self) -> dict[str, str]:
        return {
            "tone": self.tone,
            "locale": self.locale,
            "formality": self.formality,
            "template_variant": self.template_variant,
        }


# Regions where a business audience expects formal address by default. Derived
# from the dataset's regions; product should confirm (§10.2).
_FORMAL_REGIONS = frozenset({"DE", "FR", "ES"})


class PersonalisationResolver:
    def resolve(
        self,
        customer_type: str | None,
        region: str | None,
        language: str | None,
        segment: str | None = None,
    ) -> Personalisation:
        tone = self._tone(customer_type, segment)
        locale, missing = self._locale(language)
        formality = "formal" if (region in _FORMAL_REGIONS or tone == Tone.DEFERENTIAL) else "neutral"
        return Personalisation(
            tone=tone,
            locale=locale,
            formality=formality,
            template_variant=f"{tone}.{locale}",
            locale_missing=missing,
        )

    @staticmethod
    def _tone(customer_type: str | None, segment: str | None) -> str:
        if customer_type == CustomerType.corporate.value:
            return Tone.DEFERENTIAL
        if customer_type == CustomerType.SME.value:
            # A frequent SME booker wants speed; a new one wants reassurance.
            return Tone.EFFICIENT if segment == Segment.frequent.value else Tone.WARM
        return Tone.WARM

    @staticmethod
    def _locale(language: str | None) -> tuple[str, bool]:
        if language in SUPPORTED_LOCALES:
            return language, False
        # Unknown language: English is the only thing we can render safely, but
        # doing that silently is how a missing translation ships unnoticed.
        return DEFAULT_LOCALE, True
