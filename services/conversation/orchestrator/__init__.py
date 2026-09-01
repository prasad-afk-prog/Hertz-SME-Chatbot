"""M08 — Conversation Orchestrator (POA/08).

    from services.conversation.orchestrator import ConversationOrchestrator

See `service.py` for the five failure points and why each has a different
correct behaviour, and `context.py` for the precise PII claim this module makes.
"""
from .context import (
    ALLOWED_BOOKING_FIELDS,
    ALLOWED_CUSTOMER_FIELDS,
    ALLOWED_SIGNAL_FIELDS,
    FREE_TEXT_FIELDS,
    PII_FIELD_NAMES,
    ContextAssembler,
    ContextBundle,
    DatasetProfileAdapter,
    ProfileAdapter,
)
from .personalisation import Personalisation, PersonalisationResolver, Tone
from .prompts import (
    CONTEXT_CLOSE,
    CONTEXT_OPEN,
    GUARDRAILS,
    PROMPT_VERSION,
    BuiltPrompt,
    PromptBuilder,
)
from .service import ConversationOrchestrator, FailedStage, FireResult, Outcome
from .state import (
    Conversation,
    ConversationStatus,
    ConversationStore,
    Deadline,
    InMemoryConversationStore,
    InMemoryReservationClient,
    ReservationClient,
    Turn,
    TurnRole,
    new_conversation_id,
)

__all__ = [
    "ALLOWED_BOOKING_FIELDS",
    "ALLOWED_CUSTOMER_FIELDS",
    "ALLOWED_SIGNAL_FIELDS",
    "BuiltPrompt",
    "CONTEXT_CLOSE",
    "CONTEXT_OPEN",
    "Conversation",
    "ConversationOrchestrator",
    "ConversationStatus",
    "ConversationStore",
    "ContextAssembler",
    "ContextBundle",
    "DatasetProfileAdapter",
    "Deadline",
    "FREE_TEXT_FIELDS",
    "FailedStage",
    "FireResult",
    "GUARDRAILS",
    "InMemoryConversationStore",
    "InMemoryReservationClient",
    "Outcome",
    "PII_FIELD_NAMES",
    "PROMPT_VERSION",
    "Personalisation",
    "PersonalisationResolver",
    "ProfileAdapter",
    "PromptBuilder",
    "ReservationClient",
    "Tone",
    "Turn",
    "TurnRole",
    "new_conversation_id",
]
