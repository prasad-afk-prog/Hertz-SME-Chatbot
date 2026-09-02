"""M13 Admin Console & Trigger Configuration — POA/13 acceptance criteria (§6).

The four §6 criteria this suite covers:
  1. an admin can create/enable/disable/tune a trigger and change caps/waits;
  2. every change is captured in an immutable audit log with actor + before/after;
  3. invalid configs are rejected with clear errors;
  4. versions can be viewed, diffed and rolled back.

(The fifth, RBAC, needs M15's auth model — see POA/13 §11.)

Two properties worth calling out, because they are the ones a naive
implementation gets wrong:

* **rollback creates a version, it never deletes one** — otherwise the audit has
  a hole exactly where someone reverted a bad config;
* **the audit is genuinely immutable** — §7 asks for this by name, and an
  append-only list a caller can `.pop()` is not immutable.
"""
from __future__ import annotations

import pytest

from generator.fixtures import default_routing_rules, default_triggers
from generator.models import (
    Deferred,
    FrequencyCap,
    RoutingRule,
    SignalType,
    TriggerConfig,
    TriggerMatch,
    TriggerType,
)
from services.admin.config import (
    AuditAction,
    ConfigEntity,
    ConfigService,
    ConfigValidationError,
    ConfigValidator,
    GlobalSettings,
    InMemoryChangePublisher,
    NullDSLValidator,
)


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


@pytest.fixture
def svc() -> ConfigService:
    return ConfigService(publisher=InMemoryChangePublisher(), clock=FakeClock())


def trigger(
    tid="custom_v1",
    signal=SignalType.search_no_convert,
    precedence=999,
    ttype=TriggerType.in_session,
    deferred=None,
    cap_max=1,
    template=None,
) -> TriggerConfig:
    return TriggerConfig(
        trigger_id=tid,
        match=TriggerMatch(signal_type=signal),
        type=ttype,
        deferred=deferred,
        frequency_cap=FrequencyCap(per="P7D", max=cap_max),
        precedence=precedence,
        message_template_ref=template if template is not None else f"tmpl_{signal.value}",
    )


# =========================================================================== #
# The shipped fixtures must pass their own validator
# =========================================================================== #
def test_the_seeded_config_is_valid(svc):
    """A validator its own seed data fails is a validator nobody will trust."""
    issues = svc.validate_all()
    blocking = svc.validator.blocking(issues)
    assert blocking == [], f"the shipped fixtures fail validation: {[str(i) for i in blocking]}"


def test_seeding_loads_every_fixture_trigger_and_rule(svc):
    assert len(svc.triggers()) == len(default_triggers())
    assert len(svc.routing_rules()) == len(default_routing_rules())


def test_triggers_are_returned_in_precedence_order(svc):
    precedences = [t.precedence for t in svc.triggers()]
    assert precedences == sorted(precedences, reverse=True)


def test_fixtures_are_read_never_written(svc):
    """`generator/fixtures.py` is the dataset's seed config and Prasad's file
    under POA/18 §2. M13 reads it and owns its own store."""
    before = [t.model_dump(mode="json") for t in default_triggers()]
    svc.set_enabled(ConfigEntity.trigger, before[0]["trigger_id"], False, actor="admin")
    assert [t.model_dump(mode="json") for t in default_triggers()] == before


# =========================================================================== #
# §6.1 — create / enable / disable / tune
# =========================================================================== #
def test_create_a_trigger(svc):
    version = svc.upsert_trigger(trigger(), actor="admin@hertz")
    assert version.version == 1
    assert version.updated_by == "admin@hertz"
    assert any(t.trigger_id == "custom_v1" for t in svc.triggers())


def test_tune_a_frequency_cap_without_a_deploy(svc):
    svc.upsert_trigger(trigger(cap_max=1), actor="admin")
    svc.upsert_trigger(trigger(cap_max=3), actor="admin", note="raised for the promo")

    live = next(t for t in svc.triggers() if t.trigger_id == "custom_v1")
    assert live.frequency_cap.max == 3
    assert len(svc.versions(ConfigEntity.trigger, "custom_v1")) == 2


def test_disable_hides_a_trigger_from_the_enabled_view(svc):
    svc.upsert_trigger(trigger(), actor="admin")
    svc.set_enabled(ConfigEntity.trigger, "custom_v1", False, actor="admin")

    assert "custom_v1" not in [t.trigger_id for t in svc.triggers(enabled_only=True)]
    assert "custom_v1" in [t.trigger_id for t in svc.triggers()]


def test_instant_disable_works_even_on_a_config_that_would_fail_validation(svc):
    """§6's emergency brake. A live misbehaving config must be switchable off
    even if it could no longer be published."""
    svc.upsert_trigger(trigger(precedence=999), actor="admin")
    # Something else now occupies that precedence, so a re-publish would fail.
    svc.upsert_trigger(trigger(tid="other_v1", precedence=998), actor="admin")

    version = svc.set_enabled(ConfigEntity.trigger, "custom_v1", False, actor="oncall")
    assert version.enabled is False


def test_global_settings_are_versioned_too(svc):
    svc.update_settings(GlobalSettings(dormancy_days=120), actor="admin", note="product asked")
    assert svc.settings.dormancy_days == 120
    entries = svc.audit.entries(ConfigEntity.global_setting)
    assert entries[-1].after["dormancy_days"] == 120


def test_disabling_something_that_does_not_exist_raises(svc):
    with pytest.raises(KeyError):
        svc.set_enabled(ConfigEntity.trigger, "no_such_trigger", False, actor="admin")


# =========================================================================== #
# §6.2 — immutable audit with actor and before/after
# =========================================================================== #
def test_every_change_is_audited_with_actor_and_before_after(svc):
    svc.upsert_trigger(trigger(cap_max=1), actor="alice")
    svc.upsert_trigger(trigger(cap_max=5), actor="bob")

    entries = svc.audit.entries(ConfigEntity.trigger, "custom_v1")
    assert [e.actor for e in entries] == ["alice", "bob"]
    assert [e.action for e in entries] == [AuditAction.created, AuditAction.updated]
    assert entries[0].before is None
    assert entries[1].before["frequency_cap"]["max"] == 1
    assert entries[1].after["frequency_cap"]["max"] == 5


def test_the_audit_cannot_be_edited_through_a_read(svc):
    """§7 names audit immutability. An append-only list a caller can mutate
    is not immutable."""
    svc.upsert_trigger(trigger(), actor="admin")
    entries = svc.audit.entries()
    assert isinstance(entries, tuple)

    with pytest.raises(AttributeError):
        entries[0].actor = "someone-else"          # frozen dataclass

    before = len(svc.audit)
    try:
        entries.pop()                              # type: ignore[attr-defined]
    except AttributeError:
        pass
    assert len(svc.audit) == before


def test_disable_and_enable_are_distinct_audit_actions(svc):
    svc.upsert_trigger(trigger(), actor="admin")
    svc.set_enabled(ConfigEntity.trigger, "custom_v1", False, actor="admin")
    svc.set_enabled(ConfigEntity.trigger, "custom_v1", True, actor="admin")

    actions = [e.action for e in svc.audit.entries(ConfigEntity.trigger, "custom_v1")]
    assert actions == [AuditAction.created, AuditAction.disabled, AuditAction.enabled]


# =========================================================================== #
# §6.3 — invalid configs rejected with clear errors
# =========================================================================== #
def test_duplicate_precedence_is_rejected(svc):
    """M04/M05 pick a winner when two triggers match; duplicates make that
    non-deterministic. `fixtures.py` gives every signal a distinct value."""
    svc.upsert_trigger(trigger(tid="a_v1", precedence=500), actor="admin")
    with pytest.raises(ConfigValidationError, match="duplicate_precedence"):
        svc.upsert_trigger(trigger(tid="b_v1", precedence=500), actor="admin")


def test_a_disabled_trigger_does_not_block_a_precedence(svc):
    """Only enabled triggers compete, so a disabled one must not squat on a
    precedence value forever."""
    svc.upsert_trigger(trigger(tid="a_v1", precedence=500), actor="admin")
    svc.set_enabled(ConfigEntity.trigger, "a_v1", False, actor="admin")
    svc.upsert_trigger(trigger(tid="b_v1", precedence=500), actor="admin")   # must not raise


def test_unknown_template_ref_is_rejected_against_the_real_catalogue(svc):
    """Checked against M09's FallbackCatalogue, not a string pattern — so
    renaming a template fails here."""
    with pytest.raises(ConfigValidationError, match="unknown_template_ref"):
        svc.upsert_trigger(trigger(template="tmpl_does_not_exist"), actor="admin")


def test_deferred_trigger_without_deferred_settings_is_rejected(svc):
    with pytest.raises(ConfigValidationError, match="missing_deferred"):
        svc.upsert_trigger(
            trigger(tid="d_v1", ttype=TriggerType.deferred, deferred=None), actor="admin"
        )


def test_deferred_settings_on_an_in_session_trigger_are_rejected(svc):
    """They would be silently ignored at runtime, which is worse than an error."""
    with pytest.raises(ConfigValidationError, match="deferred_on_in_session"):
        svc.upsert_trigger(
            trigger(tid="i_v1", ttype=TriggerType.in_session,
                    deferred=Deferred(wait_period="PT1H", expiry="P1D")),
            actor="admin",
        )


def test_wait_period_longer_than_expiry_is_rejected(svc):
    """The engagement would expire before it was ever eligible to fire."""
    with pytest.raises(ConfigValidationError, match="wait_after_expiry"):
        svc.upsert_trigger(
            trigger(tid="d_v1", ttype=TriggerType.deferred,
                    deferred=Deferred(wait_period="P5D", expiry="P1D")),
            actor="admin",
        )


def test_bad_duration_is_rejected(svc):
    bad = trigger(tid="x_v1")
    bad.frequency_cap.per = "not-a-duration"
    with pytest.raises(ConfigValidationError, match="bad_duration"):
        svc.upsert_trigger(bad, actor="admin")


def test_a_routing_rule_without_a_queue_is_rejected(svc):
    with pytest.raises(ConfigValidationError, match="no_queue"):
        svc.upsert_routing_rule(RoutingRule(rule_id="bad_v1", match={}, route={}), actor="admin")


def test_a_routing_set_without_a_catch_all_is_flagged(svc):
    """Advisory rather than blocking — but some customers would route nowhere."""
    validator = ConfigValidator()
    issues = validator.validate_routing_set([
        RoutingRule(rule_id="en_v1", match={"language": "en"}, route={"queue": "en"}),
    ])
    assert any(i.code == "no_catch_all_route" for i in issues)
    assert validator.blocking(issues) == [], "advisory issues must not block a publish"


def test_validation_errors_name_the_field(svc):
    """§6 wants *clear* errors — an admin has to know what to fix."""
    try:
        svc.upsert_trigger(trigger(template="tmpl_nope"), actor="admin")
    except ConfigValidationError as exc:
        issue = exc.issues[0]
        assert issue.entity_id == "custom_v1"
        assert issue.field == "message_template_ref"
        assert "tmpl_nope" in str(issue)
    else:
        pytest.fail("expected a validation error")


def test_a_rejected_publish_leaves_no_version_and_no_audit_entry(svc):
    before_versions = len(svc.versions(ConfigEntity.trigger, "custom_v1"))
    before_audit = len(svc.audit)
    with pytest.raises(ConfigValidationError):
        svc.upsert_trigger(trigger(template="tmpl_nope"), actor="admin")
    assert len(svc.versions(ConfigEntity.trigger, "custom_v1")) == before_versions
    assert len(svc.audit) == before_audit


# =========================================================================== #
# The rule-DSL seam — an explicit hole, not a guess
# =========================================================================== #
def test_match_conditions_go_through_unvalidated_and_say_so():
    """POA/13 §8 wants ONE validator shared with M04. Until it exists, a rule
    with conditions publishes — but the admin is told it was not checked."""
    validator = ConfigValidator()
    assert validator.dsl_unvalidated

    with_conditions = trigger(tid="dsl_v1")
    with_conditions.match.conditions = [{"field": "dwell_ms", "op": ">", "value": 60000}]
    issues = validator.validate_trigger(with_conditions)

    dsl_issues = [i for i in issues if i.code == NullDSLValidator.code]
    assert dsl_issues, "an unvalidated DSL must be reported, not silently accepted"
    assert "NOT validated" in dsl_issues[0].message
    assert validator.blocking(issues) == [], "it is a warning, not a blocker"


def test_a_real_dsl_validator_can_be_supplied_later():
    """The seam M04 will fill."""
    class StrictDSL:
        def validate(self, trigger):
            from services.admin.config import ValidationIssue

            return [
                ValidationIssue("bad_condition", f"unknown field {c.get('field')!r}", trigger.trigger_id)
                for c in trigger.match.conditions
                if c.get("field") not in {"dwell_ms", "step"}
            ]

    validator = ConfigValidator(dsl=StrictDSL())
    assert not validator.dsl_unvalidated

    bad = trigger(tid="dsl_v1")
    bad.match.conditions = [{"field": "nonsense", "op": "=", "value": 1}]
    issues = validator.validate_trigger(bad)
    assert validator.blocking(issues), "a real DSL validator's findings must block"


def test_conditions_free_triggers_produce_no_dsl_warning():
    validator = ConfigValidator()
    assert [i for i in validator.validate_trigger(trigger()) if i.code == NullDSLValidator.code] == []


# =========================================================================== #
# §6.4 — versions viewed, diffed, rolled back
# =========================================================================== #
def test_versions_accumulate(svc):
    for cap in (1, 2, 3):
        svc.upsert_trigger(trigger(cap_max=cap), actor="admin")
    versions = svc.versions(ConfigEntity.trigger, "custom_v1")
    assert [v.version for v in versions] == [1, 2, 3]
    assert [v.definition["frequency_cap"]["max"] for v in versions] == [1, 2, 3]


def test_diff_shows_only_what_changed(svc):
    svc.upsert_trigger(trigger(cap_max=1), actor="admin")
    svc.upsert_trigger(trigger(cap_max=9), actor="admin")

    changes = svc.diff(ConfigEntity.trigger, "custom_v1", 1, 2)
    assert set(changes) == {"frequency_cap"}
    before, after = changes["frequency_cap"]
    assert before["max"] == 1 and after["max"] == 9


def test_rollback_restores_the_old_definition(svc):
    svc.upsert_trigger(trigger(cap_max=1), actor="admin")
    svc.upsert_trigger(trigger(cap_max=99), actor="admin", note="oops")
    svc.rollback(ConfigEntity.trigger, "custom_v1", to_version=1, actor="oncall")

    live = next(t for t in svc.triggers() if t.trigger_id == "custom_v1")
    assert live.frequency_cap.max == 1


def test_rollback_creates_a_new_version_and_never_deletes_one(svc):
    """The property a naive implementation gets wrong. Overwriting would punch
    a hole in the audit exactly where someone reverted a bad config."""
    svc.upsert_trigger(trigger(cap_max=1), actor="admin")
    svc.upsert_trigger(trigger(cap_max=99), actor="admin")
    rolled = svc.rollback(ConfigEntity.trigger, "custom_v1", to_version=1, actor="oncall")

    versions = svc.versions(ConfigEntity.trigger, "custom_v1")
    assert [v.version for v in versions] == [1, 2, 3], "history must not be rewritten"
    assert rolled.version == 3
    assert rolled.restored_from == 1
    assert versions[1].definition["frequency_cap"]["max"] == 99, "the bad version is still there"


def test_rollback_is_audited_as_a_rollback(svc):
    svc.upsert_trigger(trigger(cap_max=1), actor="admin")
    svc.upsert_trigger(trigger(cap_max=99), actor="admin")
    svc.rollback(ConfigEntity.trigger, "custom_v1", to_version=1, actor="oncall")

    last = svc.audit.entries(ConfigEntity.trigger, "custom_v1")[-1]
    assert last.action is AuditAction.rolled_back
    assert last.actor == "oncall"
    assert "v1" in (last.note or "")


def test_rolling_back_to_a_missing_version_raises(svc):
    svc.upsert_trigger(trigger(), actor="admin")
    with pytest.raises(KeyError):
        svc.rollback(ConfigEntity.trigger, "custom_v1", to_version=42, actor="admin")


def test_diff_against_a_missing_version_raises(svc):
    svc.upsert_trigger(trigger(), actor="admin")
    with pytest.raises(KeyError):
        svc.diff(ConfigEntity.trigger, "custom_v1", 1, 42)


# =========================================================================== #
# §3.4 — hot reload
# =========================================================================== #
def test_every_write_publishes_a_change(svc):
    published = svc.publisher.published
    before = len(published)
    svc.upsert_trigger(trigger(), actor="admin")
    svc.set_enabled(ConfigEntity.trigger, "custom_v1", False, actor="admin")
    assert len(published) == before + 2


def test_the_published_change_is_a_version_stamp_not_a_payload(svc):
    """§3.4's consumers are all Prasad's and the contract is unagreed (POA/18
    §5). A stamp lets a consumer detect staleness and re-read; a payload would
    bake in a wire format invented on his behalf."""
    svc.upsert_trigger(trigger(), actor="admin")
    change = svc.publisher.published[-1]

    assert change.entity is ConfigEntity.trigger
    assert change.entity_id == "custom_v1"
    assert change.version == 1
    assert not hasattr(change, "definition")
    assert set(vars(change)) == {"entity", "entity_id", "version", "enabled", "at"}


def test_version_stamps_increase_so_a_consumer_can_detect_staleness(svc):
    svc.upsert_trigger(trigger(cap_max=1), actor="admin")
    svc.upsert_trigger(trigger(cap_max=2), actor="admin")
    stamps = [c.version for c in svc.publisher.published if c.entity_id == "custom_v1"]
    assert stamps == [1, 2]


def test_a_rejected_publish_notifies_nobody(svc):
    before = len(svc.publisher.published)
    with pytest.raises(ConfigValidationError):
        svc.upsert_trigger(trigger(template="tmpl_nope"), actor="admin")
    assert len(svc.publisher.published) == before
