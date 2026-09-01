"""M10 — Claim Verification Service (POA/10).

The truthfulness guarantee: no price, rate or availability claim reaches a
customer unverified.

    from services.conversation.claim_verification import (
        ClaimVerificationService, MockClientAdapter,
    )

See `service.py` for the AA -> AB -> AC flow and why resolution delegates to
`generator.reference.apply_verification` rather than reimplementing it.
"""
from .client import (
    BookingAPIClient,
    BookingAPIError,
    BookingAPITimeout,
    BookingAPIUnavailable,
    CircuitBreaker,
    MockClientAdapter,
    NoDataForKey,
    TTLCache,
)
from .http_client import (
    APIKeyAuth,
    BearerAuth,
    BookingAPIConfig,
    BookingAPIEndpoints,
    HMACAuth,
    HTTPBookingAPIClient,
    HTTPResponse,
    NoAuth,
    Transport,
    UrllibTransport,
    auth_from_env,
)
from .tolerance import DEFAULT_POLICY, FROM_PRICE_POLICY, ToleranceMode, TolerancePolicy
from .detection import (
    ClaimDetector,
    PatternClaimDetector,
    TaggedClaimDetector,
    mentions_money,
)
from .service import (
    ClaimVerificationService,
    FailureKind,
    VerificationRecord,
    VerifiedResponse,
)

__all__ = [
    "APIKeyAuth",
    "BearerAuth",
    "BookingAPIConfig",
    "BookingAPIEndpoints",
    "DEFAULT_POLICY",
    "FROM_PRICE_POLICY",
    "HMACAuth",
    "HTTPBookingAPIClient",
    "HTTPResponse",
    "NoAuth",
    "ToleranceMode",
    "TolerancePolicy",
    "Transport",
    "UrllibTransport",
    "auth_from_env",
    "BookingAPIClient",
    "BookingAPIError",
    "BookingAPITimeout",
    "BookingAPIUnavailable",
    "CircuitBreaker",
    "ClaimDetector",
    "ClaimVerificationService",
    "FailureKind",
    "MockClientAdapter",
    "NoDataForKey",
    "PatternClaimDetector",
    "TTLCache",
    "TaggedClaimDetector",
    "VerificationRecord",
    "VerifiedResponse",
    "mentions_money",
]
