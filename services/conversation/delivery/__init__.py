"""M11 — Chatbot UI Integration (HS-103) & Delivery (POA/11).

    from services.conversation.delivery import DeliveryService, DeepLinkAction

See `service.py` for why the transport is a protocol (POA/11 §10.1 is open) and
why anti-nag here is narrower than M05's frequency cap.
"""
from .service import (
    ActionKind,
    CorrelationStore,
    DeepLinkAction,
    DeliveryReceipt,
    DeliveryService,
    DeliveryStatus,
    HS103Adapter,
    InboundMessage,
    MockHS103Adapter,
)

__all__ = [
    "ActionKind",
    "CorrelationStore",
    "DeepLinkAction",
    "DeliveryReceipt",
    "DeliveryService",
    "DeliveryStatus",
    "HS103Adapter",
    "InboundMessage",
    "MockHS103Adapter",
]
