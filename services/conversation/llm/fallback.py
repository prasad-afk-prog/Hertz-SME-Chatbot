"""Fallback template catalogue (M09 node X) — POA/09 §3.3.

Deterministic, localised, context-aware messages for when the LLM is
unavailable or not confident. The customer never sees an error.

**The catalogue's hard rule: no template may assert a price, rate or
availability.** M10 exists to stop unverified claims reaching customers; a
fallback template that quotes a figure would walk straight around it, because
fallbacks are generated precisely when the pipeline is already degraded.
`tests/test_llm_fallback_service.py` asserts this over every template in every
locale — it is the most valuable test in the module.

Templates use safe named slots only (`{route}`, `{vehicle}`), and rendering with
a missing slot degrades to the generic message for that locale rather than
raising or emitting a half-filled string with a stray `{route}` in it.

Locale coverage matches `generator.reference.fallback_message` (en/de/fr/es).
An unrecognised locale falls back to `en` *and is reported*, so a missing
translation surfaces as a gap rather than silently shipping English.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from generator.models import SignalType
from generator.reference import fallback_message

SUPPORTED_LOCALES = ("en", "de", "fr", "es")
DEFAULT_LOCALE = "en"

# Per-signal, per-locale copy. Context-aware (§3.3) but claim-free.
_TEMPLATES: dict[SignalType, dict[str, str]] = {
    SignalType.search_no_convert: {
        "en": "You were comparing options for {route} — want a hand narrowing it down?",
        "de": "Sie haben Optionen für {route} verglichen — soll ich bei der Auswahl helfen?",
        "fr": "Vous compariez des options pour {route} — puis-je vous aider à choisir ?",
        "es": "Estaba comparando opciones para {route} — ¿le ayudo a elegir?",
    },
    SignalType.rate_view_no_progress: {
        "en": "Still thinking about {route}? I can talk you through the options.",
        "de": "Noch unentschlossen bei {route}? Ich erkläre Ihnen gern die Optionen.",
        "fr": "Vous hésitez encore pour {route} ? Je peux vous présenter les options.",
        "es": "¿Sigue pensando en {route}? Puedo explicarle las opciones.",
    },
    SignalType.booking_abandoned: {
        "en": "Your booking for {route} is still saved — shall we finish it?",
        "de": "Ihre Buchung für {route} ist gespeichert — sollen wir sie abschließen?",
        "fr": "Votre réservation pour {route} est enregistrée — on la termine ?",
        "es": "Su reserva para {route} sigue guardada — ¿la completamos?",
    },
    SignalType.error_hit: {
        "en": "Sorry — something went wrong on that step. Would you like me to help you retry?",
        "de": "Entschuldigung — bei diesem Schritt ist etwas schiefgelaufen. Soll ich helfen?",
        "fr": "Désolé — un problème est survenu à cette étape. Puis-je vous aider à réessayer ?",
        "es": "Lo sentimos — algo falló en ese paso. ¿Le ayudo a intentarlo de nuevo?",
    },
    SignalType.extended_dwell: {
        "en": "Taking a look at {vehicle}? Happy to answer anything about it.",
        "de": "Schauen Sie sich {vehicle} an? Ich beantworte gern Ihre Fragen.",
        "fr": "Vous regardez {vehicle} ? Je réponds volontiers à vos questions.",
        "es": "¿Está viendo {vehicle}? Con gusto respondo sus dudas.",
    },
    SignalType.session_ended_no_booking: {
        "en": "Welcome back — would you like to pick up where you left off?",
        "de": "Willkommen zurück — möchten Sie dort weitermachen, wo Sie aufgehört haben?",
        "fr": "Bon retour — souhaitez-vous reprendre où vous en étiez ?",
        "es": "Bienvenido de nuevo — ¿desea continuar donde lo dejó?",
    },
    SignalType.repeated_search: {
        "en": "You've looked at {route} a few times — want me to help you decide?",
        "de": "Sie haben {route} mehrfach angesehen — soll ich bei der Entscheidung helfen?",
        "fr": "Vous avez consulté {route} plusieurs fois — puis-je vous aider à décider ?",
        "es": "Ha consultado {route} varias veces — ¿le ayudo a decidir?",
    },
    SignalType.dormant: {
        "en": "It's been a while — can I help you arrange your next rental?",
        "de": "Lange nicht gesehen — kann ich Ihnen bei Ihrer nächsten Anmietung helfen?",
        "fr": "Cela fait un moment — puis-je vous aider pour votre prochaine location ?",
        "es": "Ha pasado un tiempo — ¿le ayudo con su próximo alquiler?",
    },
}

_SLOT = re.compile(r"\{(\w+)\}")


@dataclass
class RenderedFallback:
    text: str
    locale: str
    signal: SignalType | None
    used_generic: bool = False      # no signal-specific template matched
    locale_missing: bool = False    # asked for a locale we do not have


class FallbackCatalogue:
    """Versioned catalogue (§3.3). A candidate for admin control via M13 later,
    which is why lookup goes through one method rather than being inlined."""

    VERSION = "2026-09-01"

    def __init__(self, templates: dict[SignalType, dict[str, str]] | None = None) -> None:
        self._templates = templates if templates is not None else _TEMPLATES

    @property
    def templates(self) -> dict[SignalType, dict[str, str]]:
        return self._templates

    def all_strings(self) -> list[tuple[SignalType, str, str]]:
        """(signal, locale, text) for every template — used by the no-claims test."""
        return [
            (signal, locale, text)
            for signal, by_locale in self._templates.items()
            for locale, text in by_locale.items()
        ]

    @staticmethod
    def slots(text: str) -> set[str]:
        return set(_SLOT.findall(text))

    def render(
        self,
        signal: SignalType | None,
        locale: str,
        context: dict[str, str] | None = None,
    ) -> RenderedFallback:
        """Render the safest message we can for this signal and locale.

        Degrades rather than fails, in this order:
          1. signal + locale template, if every slot can be filled;
          2. the generic localised message (`reference.fallback_message`).

        A half-filled template — "options for {route}" shown to a customer — is
        worse than a correct generic sentence, so an unfillable slot demotes to
        generic instead of rendering.
        """
        context = context or {}
        locale_missing = locale not in SUPPORTED_LOCALES
        effective = DEFAULT_LOCALE if locale_missing else locale

        by_locale = self._templates.get(signal) if signal is not None else None
        template = by_locale.get(effective) if by_locale else None

        if template is not None:
            needed = self.slots(template)
            if needed <= set(context):
                return RenderedFallback(
                    text=template.format(**{k: context[k] for k in needed}),
                    locale=effective,
                    signal=signal,
                    locale_missing=locale_missing,
                )

        return RenderedFallback(
            text=fallback_message(effective),
            locale=effective,
            signal=signal,
            used_generic=True,
            locale_missing=locale_missing,
        )
