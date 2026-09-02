"""Config versioning, audit and hot-reload types (M13) — POA/13 §3.1, §3.4.

The shapes behind §3.1's SQL. Postgres migrations need Prasad's A1 (M15/M03),
so these are the in-memory equivalents — the *semantics* are what matter and
they do not change when a real database appears underneath.

**One rule that is easy to get wrong: rollback creates a new version, it never
deletes one.** §2 requires that every change creates a version and §6 requires
an immutable audit. Rolling back to v3 must therefore produce v5 whose
definition equals v3's. Overwriting instead would punch a hole in the trail at
exactly the moment someone reverted a bad config — which is when the trail
matters most.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConfigEntity(str, Enum):
    trigger = "trigger"
    routing_rule = "routing_rule"
    global_setting = "global_setting"


class AuditAction(str, Enum):
    created = "created"
    updated = "updated"
    enabled = "enabled"
    disabled = "disabled"
    rolled_back = "rolled_back"
    deleted = "deleted"


@dataclass(frozen=True)
class ConfigVersion:
    """One immutable revision. `definition` is the serialised config object."""
    entity: ConfigEntity
    entity_id: str
    version: int
    definition: dict[str, Any]
    enabled: bool
    updated_by: str
    updated_at: float
    note: str | None = None
    # Set when this version was produced by a rollback, naming the version it
    # restored — so the history reads as a story rather than a mystery jump.
    restored_from: int | None = None


@dataclass(frozen=True)
class AuditEntry:
    """§3.1's `config_audit` row. Frozen: an audit entry is a historical fact."""
    entity: ConfigEntity
    entity_id: str
    action: AuditAction
    actor: str
    at: float
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    version: int | None = None
    note: str | None = None


@dataclass(frozen=True)
class ConfigChange:
    """What a publish emits for hot reload (§3.4).

    **The version stamp, not the payload.** §3.4's consumers are M04/M05/M06/M07
    — all Prasad's — and the contract is unagreed. Emitting
    `(entity, entity_id, version)` lets a consumer detect staleness and re-read
    from the source it already trusts. Emitting the serialised config would bake
    in a wire format invented on his behalf, and a stale-payload race on top.
    """
    entity: ConfigEntity
    entity_id: str
    version: int
    enabled: bool
    at: float


@dataclass
class GlobalSettings:
    """§2's global knobs. Named fields rather than a free-form key/value table,
    so a typo is a failure at write time instead of a silent no-op at read time.

    Defaults mirror the dataset's documented assumptions (`GenConfig`,
    `fixtures.py`) so the initial config matches what the test data was built
    against."""
    dormancy_days: int = 90
    default_cap_per: str = "P7D"
    default_cap_max: int = 1
    default_wait_period: str = "PT0S"
    default_expiry: str = "P3D"
    engagement_cooldown: str = "PT30M"

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def now() -> float:
    return time.time()


@dataclass
class ValidationIssue:
    """One problem with a proposed config. `field` is the path when known."""
    code: str
    message: str
    entity_id: str | None = None
    field: str | None = None

    def __str__(self) -> str:            # what an admin actually reads
        where = f" [{self.entity_id}{'.' + self.field if self.field else ''}]" if self.entity_id else ""
        return f"{self.code}: {self.message}{where}"


class ConfigValidationError(ValueError):
    """Raised on publish when validation fails (§6: rejected with clear errors)."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(str(i) for i in issues))
