"""M12 — Customer Response & Multi-turn Conversation Manager (POA/12).

    from services.conversation.response import ResponseManager

See `service.py` for the AE–AK state machine, `handoff.py` for why the handoff
event routes through M04 rather than straight to M07, and `resolution.py` for
why `resolved` has to be earned.
"""
from .handoff import (
    HandoffEvent,
    HandoffReason,
    HandoffSink,
    InMemoryHandoffSink,
    TranscriptTurn,
)
from .resolution import (
    NEVER_BOT_RESOLVABLE,
    AttributionWindow,
    BookingSignal,
    ResolutionDetector,
    ResolutionOutcome,
    Verdict,
)
from .service import ConversationOutcome, ResponseManager, TurnResult

__all__ = [
    "AttributionWindow",
    "BookingSignal",
    "ConversationOutcome",
    "HandoffEvent",
    "HandoffReason",
    "HandoffSink",
    "InMemoryHandoffSink",
    "NEVER_BOT_RESOLVABLE",
    "ResolutionDetector",
    "ResolutionOutcome",
    "ResponseManager",
    "TranscriptTurn",
    "TurnResult",
    "Verdict",
]
