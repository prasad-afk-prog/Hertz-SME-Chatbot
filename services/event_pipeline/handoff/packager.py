"""Context packaging (POA/07 §3.2) — assemble the payload the agent sees.

Carries refs + a short human-readable summary, not inlined PII (M15 §4): the
transcript summary from M08/M12, the triggering signal / booking refs, and the
unresolved claim (if M10 couldn't verify one). The full transcript/profile is
fetched by the agent tool from the conversation store via `conversation_id`.
"""
from __future__ import annotations

from generator.models import HandoffRequest


def summary_of(request: HandoffRequest, priority: str) -> str:
    parts = [
        f"[{priority}] {request.reason.value.replace('_', ' ')}",
        f"customer {request.customer_id} ({request.language})",
    ]
    if request.booking_reference:
        parts.append(f"booking {request.booking_reference}")
    if request.unresolved_claim:
        parts.append(f"unresolved: {request.unresolved_claim}")
    if request.transcript_summary:
        parts.append(request.transcript_summary)
    return " — ".join(parts)


def package(request: HandoffRequest, *, queue: str, skill: str | None,
            priority: str, sla: dict | None) -> dict:
    """The structured payload dispatched into the agent queue (POA/07 §3.3)."""
    return {
        "conversation_id": request.conversation_id,
        "customer_id": request.customer_id,
        "queue": queue,
        "skill": skill,
        "priority": priority,
        "sla": sla,
        "reason": request.reason.value,
        "summary": summary_of(request, priority),
        "context": {
            "event_id": request.event_id,
            "booking_reference": request.booking_reference,
            "unresolved_claim": request.unresolved_claim,
            "language": request.language,
            "customer_type": request.customer_type,
        },
    }
