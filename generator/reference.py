"""Reference decision logic — a tiny, pure implementation of the trust-critical
decisions the real services must make. It lets the test suite prove the dataset
drives the right outcomes BEFORE the real M04–M12 services exist, and it doubles
as an executable spec of those contracts.

Covered here (pure functions over the contract models):
  * M05  frequency cap (sliding window)
  * M09  availability/confidence decision (use LLM vs fallback)
  * M10  claim resolution (correct / strip) once a VerifyResult is known
  * M12  terminal-state decision (no_engagement / converted / handed_off)
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .durations import parse_duration
from .models import (
    BookingClaim,
    FrequencyCap,
    LLMResponse,
    MessageKind,
    TerminalState,
    VerifyResult,
    VerifyStatus,
)

_KIND_PRIORITY = {
    MessageKind.llm: 0,
    MessageKind.verified: 1,
    MessageKind.corrected: 2,
    MessageKind.stripped: 3,
}


# --------------------------------------------------------------------------- #
# M05 — frequency cap (sliding window)
# --------------------------------------------------------------------------- #
def would_fire(prior_fire_times: list[datetime], now: datetime, cap: FrequencyCap) -> bool:
    window = parse_duration(cap.per)
    recent = [t for t in prior_fire_times if now - t < window]
    return len(recent) < cap.max


# --------------------------------------------------------------------------- #
# M09 — availability / confidence decision
# --------------------------------------------------------------------------- #
def decide_llm(result: LLMResponse | None, threshold: float = 0.5) -> str:
    """Return 'use' or 'fallback'. `None` = provider unavailable/timeout."""
    if result is None:
        return "fallback"
    if not result.text.strip():
        return "fallback"
    if result.confidence is not None and result.confidence < threshold:
        return "fallback"
    return "use"


# --------------------------------------------------------------------------- #
# M10 — claim resolution once verification results are known
# --------------------------------------------------------------------------- #
_STRIP_PHRASE = "the current live rate"


def apply_verification(
    text: str,
    claims: list[BookingClaim],
    results: list[VerifyResult],
) -> tuple[str, MessageKind]:
    """Rewrite the draft so NO unverified claim survives.

    * ok            -> leave token, mark verified
    * wrong         -> replace token with the live value (correct_token)
    * unverifiable  -> replace token with a safe phrase (strip the claim)
    """
    if not claims:
        return text, MessageKind.llm

    delivered = text
    kind = MessageKind.llm
    for claim, res in zip(claims, results):
        if res.status == VerifyStatus.ok:
            kind = _max_kind(kind, MessageKind.verified)
        elif res.status == VerifyStatus.wrong:
            replacement = res.correct_token or _STRIP_PHRASE
            delivered = delivered.replace(claim.text_token, replacement)
            kind = _max_kind(kind, MessageKind.corrected)
        else:  # unverifiable
            delivered = delivered.replace(claim.text_token, _STRIP_PHRASE)
            kind = _max_kind(kind, MessageKind.stripped)
    return delivered, kind


def _max_kind(a: MessageKind, b: MessageKind) -> MessageKind:
    return a if _KIND_PRIORITY[a] >= _KIND_PRIORITY[b] else b


# --------------------------------------------------------------------------- #
# M09 — localised fallback templates (safe, no unverified claims)
# --------------------------------------------------------------------------- #
_FALLBACK = {
    "en": "Can I help you finish your booking?",
    "de": "Kann ich Ihnen helfen, Ihre Buchung abzuschließen?",
    "fr": "Puis-je vous aider à finaliser votre réservation ?",
    "es": "¿Puedo ayudarle a completar su reserva?",
}


def fallback_message(language: str) -> str:
    return _FALLBACK.get(language, _FALLBACK["en"])


# --------------------------------------------------------------------------- #
# M12 — terminal state
# --------------------------------------------------------------------------- #
def terminal_state(responded: bool, resolves: bool | None) -> TerminalState:
    if not responded:
        return TerminalState.no_engagement
    if resolves:
        return TerminalState.converted
    return TerminalState.handed_off
