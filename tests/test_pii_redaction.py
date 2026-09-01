"""S4 — PII redaction fixtures (POA/16 §16.5, M15 §4 as source of truth).

What these prove, beyond "the objects validate":

  * every synthetic value is **provably not real** — cards fail Luhn, emails sit
    on RFC 2606 reserved domains, phones sit in Ofcom's drama block, IPs are
    RFC 5737 TEST-NET-1. The tests assert the *property*, not the format, so
    swapping in a plausible real-looking value fails the suite;
  * spans are exact — `text[start:end] == value` — so redaction never guesses;
  * both §16.5 categories are covered: obvious (labelled) and embedded (PII
    sitting naturally inside conversational prose);
  * redaction removes all PII **and nothing else** — over-redaction is checked
    explicitly, because "no PII survives" passes trivially for a redactor that
    destroys the whole message;
  * the data-dictionary marking still matches the models.
"""
from __future__ import annotations

import re

import pytest

from generator import models as m
from generator.pii import (
    DRAMA_PHONE_E164_PREFIX,
    DRAMA_PHONE_PREFIX,
    FAKE_CARDS,
    NOT_PII,
    PII_FIELDS,
    RESERVED_EMAIL_DOMAINS,
    TEST_NET_1,
    RedactionFixtureBuilder,
    _luhn_ok,
    redaction_token,
)
from generator.models import PIICategory, PIIKind
from generator.reference import redact


@pytest.fixture(scope="module")
def fixtures():
    return RedactionFixtureBuilder().all()


# --- coverage --------------------------------------------------------------- #
def test_every_pii_kind_has_a_fixture(fixtures):
    covered = {s.kind for f in fixtures for s in f.spans}
    assert covered == set(PIIKind)


def test_both_mandated_categories_are_present(fixtures):
    cats = {f.category for f in fixtures}
    assert cats == {PIICategory.obvious, PIICategory.embedded}


def test_embedded_fixtures_are_the_majority(fixtures):
    """§16.5 calls out embedded PII specifically; labelled fields are the easy
    case and must not dominate the set."""
    embedded = [f for f in fixtures if f.category is PIICategory.embedded]
    assert len(embedded) > len(fixtures) / 2


def test_fixture_ids_unique(fixtures):
    ids = [f.fixture_id for f in fixtures]
    assert len(ids) == len(set(ids))


def test_there_is_a_negative_control(fixtures):
    """At least one fixture with no PII at all — otherwise an over-eager
    redactor looks correct everywhere."""
    clean = [f for f in fixtures if not f.spans]
    assert clean, "no PII-free fixture: over-redaction would go undetected"


# --- the values cannot be real ---------------------------------------------- #
def test_card_numbers_fail_luhn(fixtures):
    """Every issuable card passes Luhn, so a Luhn-failing string cannot be
    anyone's real card. This is the guarantee, not the '4111' prefix."""
    cards = [s.value for f in fixtures for s in f.spans if s.kind is PIIKind.payment_card]
    assert cards
    for card in cards:
        assert not _luhn_ok(card), f"{card} passes Luhn — it could be a real card number"


def test_luhn_helper_is_correct():
    """Guards the guard: if _luhn_ok were broken, the test above would pass
    vacuously."""
    assert _luhn_ok("4111 1111 1111 1111")     # canonical valid test number
    assert not _luhn_ok("4111 1111 1111 1112")


def test_emails_use_reserved_domains(fixtures):
    """RFC 2606 / RFC 6761 reserve these; they can never route to a real inbox."""
    emails = [s.value for f in fixtures for s in f.spans if s.kind is PIIKind.email]
    assert emails
    for email in emails:
        domain = email.rsplit("@", 1)[1]
        assert domain in RESERVED_EMAIL_DOMAINS, f"{email} is not on a reserved domain"


def test_phones_are_in_the_ofcom_drama_block(fixtures):
    """07700 900000-900999 is reserved by Ofcom for fiction — never allocated."""
    phones = [s.value for f in fixtures for s in f.spans if s.kind is PIIKind.phone]
    assert phones
    for phone in phones:
        assert phone.startswith((DRAMA_PHONE_PREFIX, DRAMA_PHONE_E164_PREFIX)), phone
        last3 = phone[-3:]
        assert last3.isdigit() and 0 <= int(last3) <= 999


def test_ip_addresses_are_test_net_1(fixtures):
    """RFC 5737 reserves 192.0.2.0/24 for documentation."""
    ips = [s.value for f in fixtures for s in f.spans if s.kind is PIIKind.ip_address]
    assert ips
    for ip in ips:
        assert ip.startswith(TEST_NET_1), f"{ip} is outside TEST-NET-1"


def test_no_fixture_contains_a_plausible_real_email_domain(fixtures):
    """Belt and braces across the whole corpus, not just tagged spans — catches
    a real address pasted into prose without being marked as a span."""
    real_looking = re.compile(r"[\w.+-]+@(?!example\.(?:com|org|net)\b)(?!\w+\.invalid\b)[\w.-]+\.\w+")
    for f in fixtures:
        assert not real_looking.search(f.text), f"{f.fixture_id} contains an unreserved email domain"


# --- spans are exact --------------------------------------------------------- #
def test_spans_match_the_text_exactly(fixtures):
    for f in fixtures:
        for s in f.spans:
            assert f.text[s.start : s.end] == s.value, f"{f.fixture_id}/{s.kind.value}"


def test_spans_do_not_overlap(fixtures):
    for f in fixtures:
        ordered = sorted(f.spans, key=lambda s: s.start)
        for a, b in zip(ordered, ordered[1:]):
            assert b.start >= a.end, f"{f.fixture_id}: {a.kind} overlaps {b.kind}"


def test_each_pii_value_occurs_once_in_its_fixture(fixtures):
    """A value appearing twice would make its span ambiguous — the builder
    rejects it, and this pins the guarantee."""
    for f in fixtures:
        for s in f.spans:
            assert f.text.count(s.value) == 1, f"{f.fixture_id}: {s.value!r} is not unique"


# --- redaction removes all PII ... ------------------------------------------- #
def test_redact_matches_the_pinned_expected_output(fixtures):
    for f in fixtures:
        assert redact(f.text, f.spans) == f.redacted, f.fixture_id


def test_no_pii_value_survives_redaction(fixtures):
    for f in fixtures:
        out = redact(f.text, f.spans)
        for s in f.spans:
            assert s.value not in out, f"{f.fixture_id}: {s.kind.value} survived redaction"


def test_every_span_leaves_a_labelled_placeholder(fixtures):
    """The replacement names the kind, so downstream can tell what was removed."""
    for f in fixtures:
        out = redact(f.text, f.spans)
        for s in f.spans:
            assert redaction_token(s.kind) in out, f"{f.fixture_id}/{s.kind.value}"


# --- ... and nothing else ----------------------------------------------------- #
def test_redaction_does_not_destroy_surrounding_text(fixtures):
    """The over-redaction guard. Without it, a redactor that returns '' passes
    every 'no PII survives' assertion above."""
    for f in fixtures:
        out = redact(f.text, f.spans)
        for keep in f.preserves:
            assert keep in out, f"{f.fixture_id}: over-redacted, lost {keep!r}"


def test_every_fixture_asserts_something_survives(fixtures):
    for f in fixtures:
        assert f.preserves, f"{f.fixture_id} has no `preserves` — over-redaction untested"


def test_clean_text_is_returned_unchanged(fixtures):
    clean = next(f for f in fixtures if not f.spans)
    assert redact(clean.text, clean.spans) == clean.text


def test_redaction_is_idempotent(fixtures):
    """Redacting already-redacted text must not corrupt the placeholders."""
    for f in fixtures:
        once = redact(f.text, f.spans)
        assert redact(once, []) == once, f.fixture_id


# --- redact() rejects bad input rather than mangling it ---------------------- #
def test_redact_rejects_drifted_spans(fixtures):
    f = next(f for f in fixtures if f.spans)
    tampered = f.text.replace(f.spans[0].value, "x" * len(f.spans[0].value), 1)
    with pytest.raises(ValueError, match="drifted apart"):
        redact(tampered, f.spans)


def test_redact_rejects_overlapping_spans(fixtures):
    f = next(f for f in fixtures if len(f.spans) >= 1)
    s = f.spans[0]
    overlapping = [s, m.PIISpan(kind=PIIKind.email, start=s.start, end=s.end + 1, value="x")]
    with pytest.raises(ValueError, match="overlapping"):
        redact(f.text, overlapping)


# --- the data dictionary stays in step with the models ----------------------- #
def test_marked_pii_fields_still_exist_on_their_models():
    """§16.5 asks for PII fields to be marked. This catches the marking rotting
    when the models change — e.g. Prasad's S1/S2 work on the same file."""
    for model_name, fields in PII_FIELDS.items():
        model = getattr(m, model_name, None)
        assert model is not None, f"PII_FIELDS names {model_name}, which no longer exists"
        for field in fields:
            assert field in model.model_fields, f"{model_name}.{field} no longer exists"


def test_not_pii_entries_also_still_exist():
    for dotted in NOT_PII:
        model_name, field = dotted.split(".")
        model = getattr(m, model_name, None)
        assert model is not None, f"NOT_PII names {model_name}, which no longer exists"
        assert field in model.model_fields, f"{dotted} no longer exists"


def test_contact_is_fully_marked():
    """Contact is the one model in the current schema that is all-PII; if a
    field is added there it must be classified, not silently ignored."""
    assert set(m.Contact.model_fields) == set(PII_FIELDS["Contact"])
