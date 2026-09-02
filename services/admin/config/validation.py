"""Config validation (M13 §3.3) — and an explicit hole where the DSL goes.

§6 requires invalid configs to be rejected with clear errors. This validates
everything that can be checked from the config itself plus the artefacts it
references:

* schema and enum validity (Pydantic already does most of this);
* **referenced templates exist** — checked against M09's real `FallbackCatalogue`,
  not a string pattern, so renaming a template fails here;
* **precedence is distinct across enabled triggers** — `fixtures.py` gives every
  signal its own value (200 down to 50) because M04/M05 pick a winner when two
  triggers match; duplicates make that pick non-deterministic;
* frequency caps and ISO-8601 durations are sane;
* deferred-only fields appear only on deferred triggers;
* routing rules have a reachable catch-all, or some customers route nowhere.

**What is deliberately NOT validated: `TriggerMatch.conditions`.**

That is the rule DSL, it is currently `list[dict]` with no schema, and POA/13 §8
says the mitigation for DSL divergence is a *"single shared validator library"*
shared with M04. M04 is Prasad's and does not exist yet. Writing a second
validator here would manufacture exactly the divergence §8 warns about — the
runtime would accept rules the admin console rejects, or worse, the reverse.

So the seam is named rather than filled: `DSLValidator` is a protocol,
`NullDSLValidator` is the honest default that validates nothing and says so, and
`ConfigValidator.dsl_unvalidated` is True until a real one is supplied. A hole
with a shape is more useful to Prasad than either a guess or silence.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from generator.durations import parse_duration
from generator.models import RoutingRule, SignalType, TriggerConfig, TriggerType
from services.conversation.llm.fallback import FallbackCatalogue

from .models import ValidationIssue


@runtime_checkable
class DSLValidator(Protocol):
    """Validates `TriggerMatch.conditions`. To be shared with M04 (§8)."""

    def validate(self, trigger: TriggerConfig) -> list[ValidationIssue]: ...


class NullDSLValidator:
    """The honest default: validates nothing, and is visible about it.

    Returns an informational issue (never an error) when a trigger actually
    carries conditions, so an admin publishing a rule with an unvalidated DSL
    knows it went through unchecked rather than assuming it was approved.
    """

    code = "dsl_not_validated"

    def validate(self, trigger: TriggerConfig) -> list[ValidationIssue]:
        if not trigger.match.conditions:
            return []
        return [ValidationIssue(
            code=self.code,
            message=(
                f"{len(trigger.match.conditions)} match condition(s) were NOT validated — "
                "the shared rule-DSL validator (POA/13 §8, shared with M04) does not exist yet"
            ),
            entity_id=trigger.trigger_id,
            field="match.conditions",
        )]


class ConfigValidator:
    """§3.3's validator. Errors block a publish; informational issues do not."""

    #: Issue codes that are advisory rather than blocking.
    ADVISORY = frozenset({NullDSLValidator.code, "no_catch_all_route"})

    def __init__(
        self,
        catalogue: FallbackCatalogue | None = None,
        dsl: DSLValidator | None = None,
    ) -> None:
        self.catalogue = catalogue or FallbackCatalogue()
        self.dsl = dsl or NullDSLValidator()

    @property
    def dsl_unvalidated(self) -> bool:
        """True while the rule DSL is going through unchecked."""
        return isinstance(self.dsl, NullDSLValidator)

    def blocking(self, issues: list[ValidationIssue]) -> list[ValidationIssue]:
        return [i for i in issues if i.code not in self.ADVISORY]

    # ---- triggers -------------------------------------------------------- #
    def validate_trigger(
        self, trigger: TriggerConfig, others: list[TriggerConfig] | None = None
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        tid = trigger.trigger_id

        if not tid.strip():
            issues.append(ValidationIssue("empty_id", "trigger_id must not be blank"))

        # Referenced template must actually exist in M09's catalogue.
        ref = trigger.message_template_ref
        if ref:
            expected = f"tmpl_{trigger.match.signal_type.value}"
            known = self.catalogue.templates.get(trigger.match.signal_type)
            if not known:
                issues.append(ValidationIssue(
                    "missing_template",
                    f"no fallback template exists for signal {trigger.match.signal_type.value!r}",
                    tid, "message_template_ref",
                ))
            elif ref != expected:
                issues.append(ValidationIssue(
                    "unknown_template_ref",
                    f"template_ref {ref!r} does not match the catalogue entry {expected!r}",
                    tid, "message_template_ref",
                ))

        # Frequency cap.
        cap = trigger.frequency_cap
        if cap.max < 0:
            issues.append(ValidationIssue(
                "negative_cap", "frequency_cap.max must not be negative", tid, "frequency_cap.max"))
        issues += self._duration(cap.per, tid, "frequency_cap.per")

        # Deferred fields only on deferred triggers, and vice versa.
        if trigger.type is TriggerType.deferred:
            if trigger.deferred is None:
                issues.append(ValidationIssue(
                    "missing_deferred",
                    "a deferred trigger must carry wait_period and expiry", tid, "deferred"))
            else:
                issues += self._duration(trigger.deferred.wait_period, tid, "deferred.wait_period")
                issues += self._duration(trigger.deferred.expiry, tid, "deferred.expiry")
                if self._seconds(trigger.deferred.wait_period) >= self._seconds(trigger.deferred.expiry):
                    issues.append(ValidationIssue(
                        "wait_after_expiry",
                        "wait_period must be shorter than expiry, or the engagement "
                        "expires before it is ever eligible to fire",
                        tid, "deferred.wait_period",
                    ))
        elif trigger.deferred is not None:
            issues.append(ValidationIssue(
                "deferred_on_in_session",
                "an in-session trigger must not carry deferred settings — they would "
                "be silently ignored at runtime",
                tid, "deferred",
            ))

        # Precedence must be distinct among ENABLED triggers.
        for other in others or []:
            if other.trigger_id == tid:
                continue
            if other.precedence == trigger.precedence:
                issues.append(ValidationIssue(
                    "duplicate_precedence",
                    f"precedence {trigger.precedence} is already used by {other.trigger_id!r}; "
                    "M04/M05 could not pick a winner deterministically",
                    tid, "precedence",
                ))

        issues += self.dsl.validate(trigger)
        return issues

    # ---- routing rules --------------------------------------------------- #
    def validate_routing_rule(self, rule: RoutingRule) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        rid = rule.rule_id

        if not rid.strip():
            issues.append(ValidationIssue("empty_id", "rule_id must not be blank"))
        if not rule.route.get("queue"):
            issues.append(ValidationIssue(
                "no_queue", "a routing rule must name a queue", rid, "route.queue"))
        if rule.sla:
            for key, value in rule.sla.items():
                issues += self._duration(str(value), rid, f"sla.{key}")
        return issues

    def validate_routing_set(self, rules: list[RoutingRule]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for rule in rules:
            issues += self.validate_routing_rule(rule)

        ids = [r.rule_id for r in rules]
        for duplicate in {i for i in ids if ids.count(i) > 1}:
            issues.append(ValidationIssue("duplicate_rule_id", f"rule_id {duplicate!r} is not unique"))

        if rules and not any(not r.match for r in rules):
            issues.append(ValidationIssue(
                "no_catch_all_route",
                "no rule with an empty match — some customers would route nowhere",
            ))
        return issues

    def validate_trigger_set(self, triggers: list[TriggerConfig]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        seen: list[TriggerConfig] = []
        for trigger in triggers:
            issues += self.validate_trigger(trigger, seen)
            seen.append(trigger)

        ids = [t.trigger_id for t in triggers]
        for duplicate in {i for i in ids if ids.count(i) > 1}:
            issues.append(ValidationIssue("duplicate_trigger_id", f"trigger_id {duplicate!r} is not unique"))
        return issues

    # ---- helpers --------------------------------------------------------- #
    @staticmethod
    def _duration(value: str, entity_id: str, field: str) -> list[ValidationIssue]:
        try:
            parse_duration(value)
        except Exception:
            return [ValidationIssue(
                "bad_duration", f"{value!r} is not a valid ISO-8601 duration", entity_id, field)]
        return []

    @staticmethod
    def _seconds(value: str) -> float:
        try:
            return parse_duration(value).total_seconds()
        except Exception:
            return 0.0


def known_signal_types() -> set[str]:
    return {s.value for s in SignalType}
