"""M13 — Admin Console & Trigger Configuration (POA/13).

    from services.admin.config import ConfigService, ConfigEntity

The config-not-code loop: triggers, routing rules and global settings, versioned,
audited, and published to the runtime as version stamps.

See `validation.py` for the deliberately unfilled rule-DSL seam, and POA/13 §11
for what is deferred and why.
"""
from .models import (
    AuditAction,
    AuditEntry,
    ConfigChange,
    ConfigEntity,
    ConfigValidationError,
    ConfigVersion,
    GlobalSettings,
    ValidationIssue,
)
from .service import (
    AuditLog,
    ChangePublisher,
    ConfigService,
    InMemoryChangePublisher,
)
from .validation import ConfigValidator, DSLValidator, NullDSLValidator

__all__ = [
    "AuditAction",
    "AuditEntry",
    "AuditLog",
    "ChangePublisher",
    "ConfigChange",
    "ConfigEntity",
    "ConfigService",
    "ConfigValidationError",
    "ConfigValidator",
    "ConfigVersion",
    "DSLValidator",
    "GlobalSettings",
    "InMemoryChangePublisher",
    "NullDSLValidator",
    "ValidationIssue",
]
