"""Admin config service (M13 nodes AP, AQ) — POA/13 §2, §3.2, §3.4.

CRUD, versioning, rollback, audit and hot-reload notification for triggers,
routing rules and global settings. This is the "config-not-code" loop: an admin
changes a cap or disables a trigger and the runtime picks it up without a
deploy.

**Three rules the implementation is built around.**

*Rollback creates a version, it never deletes one.* Restoring v3 produces v5
whose definition equals v3's, and the audit records both the action and which
version was restored. Overwriting would punch a hole in the trail precisely
where someone reverted a bad config.

*The audit is append-only and reads return copies.* §7 asks for an immutability
test, and a list a caller can `.pop()` is not immutable.

*Publishing emits a version stamp, not a payload.* §3.4's consumers are all
Prasad's modules and the contract is unagreed — see POA/18 §5.

**Not here, and why:** RBAC (§5.5) needs M15's auth model, which is Prasad's A1;
the HTTP surface (§3.2) is FastAPI, a new dependency in a shared file, and the
`services/` layout is still unconfirmed; the admin UI (§5.6) is a different
stack and §10.1 has not been answered; dry-run preview (§5.7) needs the shared
DSL validator that §8 says must be shared with M04. All recorded in POA/13 §11.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

from generator.fixtures import default_routing_rules, default_triggers
from generator.models import RoutingRule, TriggerConfig

from .models import (
    AuditAction,
    AuditEntry,
    ConfigChange,
    ConfigEntity,
    ConfigValidationError,
    ConfigVersion,
    GlobalSettings,
    ValidationIssue,
    now,
)
from .validation import ConfigValidator


@runtime_checkable
class ChangePublisher(Protocol):
    """§3.4 hot-reload. Redis pub/sub in production; this is the seam."""

    def publish(self, change: ConfigChange) -> None: ...


@dataclass
class InMemoryChangePublisher:
    published: list[ConfigChange] = field(default_factory=list)

    def publish(self, change: ConfigChange) -> None:
        self.published.append(change)


class AuditLog:
    """Append-only. Reads return a tuple, so history cannot be edited through it."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def append(self, entry: AuditEntry) -> None:
        self._entries.append(entry)

    def entries(
        self, entity: ConfigEntity | None = None, entity_id: str | None = None
    ) -> tuple[AuditEntry, ...]:
        found: Iterable[AuditEntry] = self._entries
        if entity is not None:
            found = (e for e in found if e.entity is entity)
        if entity_id is not None:
            found = (e for e in found if e.entity_id == entity_id)
        return tuple(found)

    def __len__(self) -> int:
        return len(self._entries)


class ConfigService:
    def __init__(
        self,
        validator: ConfigValidator | None = None,
        publisher: ChangePublisher | None = None,
        clock: Callable[[], float] = now,
        *,
        seed_from_fixtures: bool = True,
    ) -> None:
        self.validator = validator or ConfigValidator()
        self.publisher = publisher or InMemoryChangePublisher()
        self.clock = clock
        self.audit = AuditLog()

        self._versions: dict[tuple[ConfigEntity, str], list[ConfigVersion]] = {}
        self._settings = GlobalSettings()
        self._settings_versions: list[ConfigVersion] = []

        if seed_from_fixtures:
            # Read the dataset's seed config; never write to it. `fixtures.py`
            # is the generator's, and POA/18 §2 assigns that file to Prasad.
            for trigger in default_triggers():
                self._write(ConfigEntity.trigger, trigger.trigger_id, trigger.model_dump(mode="json"),
                            enabled=trigger.enabled, actor="system:seed",
                            action=AuditAction.created, note="seeded from generator fixtures")
            for rule in default_routing_rules():
                self._write(ConfigEntity.routing_rule, rule.rule_id, rule.model_dump(mode="json"),
                            enabled=True, actor="system:seed",
                            action=AuditAction.created, note="seeded from generator fixtures")

    # ---- internals -------------------------------------------------------- #
    def _key(self, entity: ConfigEntity, entity_id: str):
        return (entity, entity_id)

    def _write(
        self,
        entity: ConfigEntity,
        entity_id: str,
        definition: dict[str, Any],
        *,
        enabled: bool,
        actor: str,
        action: AuditAction,
        note: str | None = None,
        restored_from: int | None = None,
    ) -> ConfigVersion:
        history = self._versions.setdefault(self._key(entity, entity_id), [])
        before = history[-1].definition if history else None

        version = ConfigVersion(
            entity=entity,
            entity_id=entity_id,
            version=len(history) + 1,
            definition=definition,
            enabled=enabled,
            updated_by=actor,
            updated_at=self.clock(),
            note=note,
            restored_from=restored_from,
        )
        history.append(version)

        self.audit.append(AuditEntry(
            entity=entity, entity_id=entity_id, action=action, actor=actor,
            at=version.updated_at, before=before, after=definition,
            version=version.version, note=note,
        ))
        self.publisher.publish(ConfigChange(
            entity=entity, entity_id=entity_id, version=version.version,
            enabled=enabled, at=version.updated_at,
        ))
        return version

    def _current(self, entity: ConfigEntity, entity_id: str) -> ConfigVersion | None:
        history = self._versions.get(self._key(entity, entity_id))
        return history[-1] if history else None

    # ---- reads ------------------------------------------------------------ #
    def triggers(self, *, enabled_only: bool = False) -> list[TriggerConfig]:
        """The `config_current` view (§3.1) for triggers."""
        out = []
        for (entity, _), history in self._versions.items():
            if entity is not ConfigEntity.trigger:
                continue
            latest = history[-1]
            if enabled_only and not latest.enabled:
                continue
            out.append(TriggerConfig(**latest.definition))
        return sorted(out, key=lambda t: -t.precedence)

    def routing_rules(self) -> list[RoutingRule]:
        return [
            RoutingRule(**history[-1].definition)
            for (entity, _), history in self._versions.items()
            if entity is ConfigEntity.routing_rule
        ]

    @property
    def settings(self) -> GlobalSettings:
        return self._settings

    def versions(self, entity: ConfigEntity, entity_id: str) -> tuple[ConfigVersion, ...]:
        return tuple(self._versions.get(self._key(entity, entity_id), []))

    def version(self, entity: ConfigEntity, entity_id: str, version: int) -> ConfigVersion | None:
        for candidate in self._versions.get(self._key(entity, entity_id), []):
            if candidate.version == version:
                return candidate
        return None

    def diff(self, entity: ConfigEntity, entity_id: str, a: int, b: int) -> dict[str, tuple[Any, Any]]:
        """Field-level before/after between two versions (§2 'view/diff')."""
        left, right = self.version(entity, entity_id, a), self.version(entity, entity_id, b)
        if left is None or right is None:
            raise KeyError(f"{entity.value} {entity_id} has no version {a if left is None else b}")
        keys = set(left.definition) | set(right.definition)
        return {
            key: (left.definition.get(key), right.definition.get(key))
            for key in sorted(keys)
            if left.definition.get(key) != right.definition.get(key)
        }

    # ---- writes ----------------------------------------------------------- #
    def upsert_trigger(self, trigger: TriggerConfig, actor: str, note: str | None = None) -> ConfigVersion:
        others = [t for t in self.triggers(enabled_only=True) if t.trigger_id != trigger.trigger_id]
        issues = self.validator.validate_trigger(trigger, others)
        blocking = self.validator.blocking(issues)
        if blocking:
            raise ConfigValidationError(blocking)

        existing = self._current(ConfigEntity.trigger, trigger.trigger_id)
        return self._write(
            ConfigEntity.trigger, trigger.trigger_id, trigger.model_dump(mode="json"),
            enabled=trigger.enabled, actor=actor,
            action=AuditAction.updated if existing else AuditAction.created,
            note=note,
        )

    def upsert_routing_rule(self, rule: RoutingRule, actor: str, note: str | None = None) -> ConfigVersion:
        issues = self.validator.validate_routing_rule(rule)
        blocking = self.validator.blocking(issues)
        if blocking:
            raise ConfigValidationError(blocking)

        existing = self._current(ConfigEntity.routing_rule, rule.rule_id)
        return self._write(
            ConfigEntity.routing_rule, rule.rule_id, rule.model_dump(mode="json"),
            enabled=True, actor=actor,
            action=AuditAction.updated if existing else AuditAction.created, note=note,
        )

    def set_enabled(
        self, entity: ConfigEntity, entity_id: str, enabled: bool, actor: str, note: str | None = None
    ) -> ConfigVersion:
        """§6's 'instant disable' — the emergency brake, so it must not be
        blocked by validation. A config that is already live and misbehaving has
        to be switchable off even if it would no longer pass validation."""
        current = self._current(entity, entity_id)
        if current is None:
            raise KeyError(f"{entity.value} {entity_id!r} does not exist")
        return self._write(
            entity, entity_id, dict(current.definition), enabled=enabled, actor=actor,
            action=AuditAction.enabled if enabled else AuditAction.disabled, note=note,
        )

    def rollback(
        self, entity: ConfigEntity, entity_id: str, to_version: int, actor: str, note: str | None = None
    ) -> ConfigVersion:
        """Restore an earlier definition by writing a NEW version.

        Never rewrites or removes history: the audit must still show that a
        rollback happened and what it restored.
        """
        target = self.version(entity, entity_id, to_version)
        if target is None:
            raise KeyError(f"{entity.value} {entity_id!r} has no version {to_version}")
        return self._write(
            entity, entity_id, dict(target.definition), enabled=target.enabled, actor=actor,
            action=AuditAction.rolled_back,
            note=note or f"rolled back to v{to_version}",
            restored_from=to_version,
        )

    def update_settings(self, settings: GlobalSettings, actor: str, note: str | None = None) -> ConfigVersion:
        self._settings = settings
        history = self._settings_versions
        before = history[-1].definition if history else None
        version = ConfigVersion(
            entity=ConfigEntity.global_setting, entity_id="global", version=len(history) + 1,
            definition=settings.as_dict(), enabled=True, updated_by=actor,
            updated_at=self.clock(), note=note,
        )
        history.append(version)
        self.audit.append(AuditEntry(
            entity=ConfigEntity.global_setting, entity_id="global", action=AuditAction.updated,
            actor=actor, at=version.updated_at, before=before, after=version.definition,
            version=version.version, note=note,
        ))
        self.publisher.publish(ConfigChange(
            entity=ConfigEntity.global_setting, entity_id="global",
            version=version.version, enabled=True, at=version.updated_at,
        ))
        return version

    # ---- health ----------------------------------------------------------- #
    def validate_all(self) -> list[ValidationIssue]:
        """Validate the whole live config — for a health endpoint, and for the
        test that asserts the shipped fixtures pass their own validator."""
        issues = self.validator.validate_trigger_set(self.triggers(enabled_only=True))
        issues += self.validator.validate_routing_set(self.routing_rules())
        return issues
