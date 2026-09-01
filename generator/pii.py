"""PII redaction fixtures (S4) — POA/16 §16.5, with M15 §4 as source of truth.

Two jobs:

1. **`PII_FIELDS`** — the data-dictionary marking §16.5 asks for: which model
   fields carry PII. A test asserts every field named here still exists on its
   model, so the marking cannot silently rot when the models change.

2. **`RedactionFixtureBuilder`** — the fixture set. §16.5 requires *both*
   obvious PII (a form-like dump) and PII **embedded naturally inside
   conversational messages**, because a redactor that only handles labelled
   fields passes the first and fails catastrophically on the second.

**Every value here is synthetic and reserved-by-specification**, so a fixture
can never collide with a real person:

| Kind | Range used | Why it cannot be real |
|------|-----------|-----------------------|
| email | `example.com`, `.invalid` | RFC 2606 / RFC 6761 reserve these; never resolvable |
| phone (UK) | `07700 900000`–`900999` | Ofcom reserves this block for drama/fiction |
| phone (intl) | `+44 7700 900xxx` | same block in E.164 form |
| payment card | Luhn-**invalid** digit strings | every real card passes Luhn, so these cannot be issued |
| IP | `192.0.2.0/24` (TEST-NET-1) | RFC 5737 reserves it for documentation |
| licence / passport | documented-invalid shapes | see the constants below |

The tests assert these *properties* (Luhn fails, domain is reserved, phone is in
the drama block) rather than just the format — so a future edit that swaps in a
plausible real-looking value fails the suite instead of sneaking through.

The redaction decision itself lives in `reference.redact()`, next to the other
trust-critical decisions (M05/M09/M10/M12) — that is where a service author
looks for it.
"""
from __future__ import annotations

from .models import (
    PIICategory,
    PIIKind,
    PIISpan,
    RedactionFixture,
)

# --------------------------------------------------------------------------- #
# 1. Data dictionary — which model fields carry PII (§16.5 "mark PII fields")
# --------------------------------------------------------------------------- #
PII_FIELDS: dict[str, dict[str, PIIKind]] = {
    "Contact": {
        "name": PIIKind.full_name,
        "email": PIIKind.email,
        "phone": PIIKind.phone,
    },
    "BookingDriver": {
        "name": PIIKind.full_name,
    },
    "Booking": {
        # A reservation number is PII when it can retrieve a booking (§16.5).
        "reference_no": PIIKind.booking_reference,
    },
    "Company": {
        # Not a person, but it identifies the account and is treated as PII by
        # the same rule that covers loyalty/member numbers.
        "account_no": PIIKind.loyalty_number,
    },
}

# Fields that look sensitive but are NOT PII — recorded so the classification is
# a decision rather than an omission.
NOT_PII: dict[str, str] = {
    "Customer.customer_id": "opaque synthetic id, not a real-world identifier",
    "Customer.region": "coarse geography, not identifying on its own",
    "Company.name": "a business name is public record, not personal data",
}

# --------------------------------------------------------------------------- #
# 2. Reserved synthetic values (see the table in the module docstring)
# --------------------------------------------------------------------------- #
RESERVED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "invalid")
DRAMA_PHONE_PREFIX = "07700 900"          # Ofcom drama block, 900000-900999
DRAMA_PHONE_E164_PREFIX = "+44 7700 900"
TEST_NET_1 = "192.0.2."                   # RFC 5737

# Luhn-INVALID card numbers. A real, issuable card always passes Luhn, so these
# are structurally incapable of being anyone's card. Asserted in the tests.
FAKE_CARDS = ("4111 1111 1111 1112", "5500 0000 0000 0005")

# Format-shaped but documented-invalid identity numbers.
# UK licences encode the holder's DOB in chars 5-10; "99" is not a valid month
# code in that scheme, so this string cannot belong to a real licence.
FAKE_LICENCE = "MORGA995154SM9IJ"
FAKE_PASSPORT = "000000000"               # all-zero is not issued


def _luhn_ok(digits: str) -> bool:
    """True if the digit string passes the Luhn checksum."""
    nums = [int(c) for c in digits if c.isdigit()]
    total, parity = 0, len(nums) % 2
    for i, n in enumerate(nums):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


# --------------------------------------------------------------------------- #
# 3. Fixture builder
# --------------------------------------------------------------------------- #
REDACTION_TOKEN = "[REDACTED:{kind}]"


def redaction_token(kind: PIIKind) -> str:
    return REDACTION_TOKEN.format(kind=kind.value)


class RedactionFixtureBuilder:
    """Builds the fixture set. Spans are computed, never hand-counted."""

    def _spans(self, text: str, values: list[tuple[PIIKind, str]]) -> list[PIISpan]:
        """Locate each value in `text` and assert the location is unambiguous.

        Computing offsets at build time (rather than writing them by hand) is
        what keeps fixtures correct when someone edits the sentence — the
        alternative silently drifts.
        """
        spans: list[PIISpan] = []
        for kind, value in values:
            occurrences = text.count(value)
            if occurrences != 1:
                raise AssertionError(
                    f"value {value!r} appears {occurrences}x in fixture text; "
                    "each PII value must occur exactly once so its span is unambiguous"
                )
            start = text.index(value)
            spans.append(PIISpan(kind=kind, start=start, end=start + len(value), value=value))
        return sorted(spans, key=lambda s: s.start)

    def _expected(self, text: str, spans: list[PIISpan]) -> str:
        """Apply redaction back-to-front so earlier offsets stay valid."""
        out = text
        for span in sorted(spans, key=lambda s: s.start, reverse=True):
            out = out[: span.start] + redaction_token(span.kind) + out[span.end :]
        return out

    def _fixture(
        self,
        fixture_id: str,
        category: PIICategory,
        description: str,
        text: str,
        values: list[tuple[PIIKind, str]],
        preserves: list[str],
    ) -> RedactionFixture:
        spans = self._spans(text, values)
        return RedactionFixture(
            fixture_id=fixture_id,
            category=category,
            description=description,
            text=text,
            spans=spans,
            redacted=self._expected(text, spans),
            preserves=preserves,
        )

    # ---- the set ------------------------------------------------------- #
    def all(self) -> list[RedactionFixture]:
        fixtures = [
            *self.obvious(),
            *self.embedded(),
            *self.edge_cases(),
        ]
        covered = {s.kind for f in fixtures for s in f.spans}
        missing = set(PIIKind) - covered
        if missing:
            raise AssertionError(f"PII kinds with no fixture: {sorted(k.value for k in missing)}")
        return fixtures

    # -- obvious: labelled, form-like ------------------------------------ #
    def obvious(self) -> list[RedactionFixture]:
        return [
            self._fixture(
                "PII-OBV-01-contact-block",
                PIICategory.obvious,
                "A pasted contact block — labelled fields, the easy case.",
                "Name: Alex Morgan\n"
                "Email: alex.morgan@example.com\n"
                "Phone: 07700 900123\n"
                "Address: 14 Bridge Street, Manchester M1 2AB",
                [
                    (PIIKind.full_name, "Alex Morgan"),
                    (PIIKind.email, "alex.morgan@example.com"),
                    (PIIKind.phone, "07700 900123"),
                    (PIIKind.address, "14 Bridge Street, Manchester M1 2AB"),
                ],
                preserves=["Name:", "Email:", "Phone:", "Address:"],
            ),
            self._fixture(
                "PII-OBV-02-identity-documents",
                PIICategory.obvious,
                "Licence, passport and date of birth — the documents a rental desk asks for.",
                f"Driving licence: {FAKE_LICENCE}\n"
                f"Passport no: {FAKE_PASSPORT}\n"
                "Date of birth: 1985-04-12",
                [
                    (PIIKind.driving_licence, FAKE_LICENCE),
                    (PIIKind.passport, FAKE_PASSPORT),
                    (PIIKind.date_of_birth, "1985-04-12"),
                ],
                preserves=["Driving licence:", "Passport no:", "Date of birth:"],
            ),
            self._fixture(
                "PII-OBV-03-payment-and-account",
                PIICategory.obvious,
                "Card, loyalty number and booking reference in one block.",
                f"Card: {FAKE_CARDS[0]}\n"
                "Loyalty: HFB-LOY-8842019\n"
                "Booking ref: HFB-000123",
                [
                    (PIIKind.payment_card, FAKE_CARDS[0]),
                    (PIIKind.loyalty_number, "HFB-LOY-8842019"),
                    (PIIKind.booking_reference, "HFB-000123"),
                ],
                preserves=["Card:", "Loyalty:", "Booking ref:"],
            ),
        ]

    # -- embedded: natural conversational prose (the hard case) ---------- #
    def embedded(self) -> list[RedactionFixture]:
        return [
            self._fixture(
                "PII-EMB-01-callback-request",
                PIICategory.embedded,
                "Phone number mid-sentence, no label anywhere near it.",
                "Sure, could someone ring me back on 07700 900456 this afternoon? "
                "I'm usually free after three.",
                [(PIIKind.phone, "07700 900456")],
                preserves=["could someone ring me back on", "I'm usually free after three."],
            ),
            self._fixture(
                "PII-EMB-02-name-and-email-in-prose",
                PIICategory.embedded,
                "Name and email woven into a sentence — no field labels.",
                "It's Priya Raman here, the booking should be under "
                "priya.raman@example.org if that helps.",
                [
                    (PIIKind.full_name, "Priya Raman"),
                    (PIIKind.email, "priya.raman@example.org"),
                ],
                preserves=["here, the booking should be under", "if that helps."],
            ),
            self._fixture(
                "PII-EMB-03-card-read-aloud",
                PIICategory.embedded,
                "Card number typed into chat despite the bot asking customers not to. "
                "This must be redacted before it ever reaches the LLM (M15 §4).",
                f"my card is {FAKE_CARDS[1]} if you need it to hold the booking",
                [(PIIKind.payment_card, FAKE_CARDS[1])],
                preserves=["my card is", "if you need it to hold the booking"],
            ),
            self._fixture(
                "PII-EMB-04-address-in-a-complaint",
                PIICategory.embedded,
                "Home address inside a complaint — PII buried in free text (§16.5).",
                "The van never turned up at 9 Rowan Close, Leeds LS1 4DY and "
                "I waited two hours in the rain.",
                [(PIIKind.address, "9 Rowan Close, Leeds LS1 4DY")],
                preserves=["The van never turned up at", "I waited two hours in the rain."],
            ),
            self._fixture(
                "PII-EMB-05-multiple-kinds-one-sentence",
                PIICategory.embedded,
                "Four different kinds in one run-on sentence — order and adjacency matter.",
                "I'm Daniel Okafor, born 1990-11-03, licence "
                f"{FAKE_LICENCE[:8]}9AB2CD, booking HFB-000456 — can you check it?",
                [
                    (PIIKind.full_name, "Daniel Okafor"),
                    (PIIKind.date_of_birth, "1990-11-03"),
                    (PIIKind.driving_licence, f"{FAKE_LICENCE[:8]}9AB2CD"),
                    (PIIKind.booking_reference, "HFB-000456"),
                ],
                preserves=["can you check it?"],
            ),
            self._fixture(
                "PII-EMB-06-vehicle-and-ip",
                PIICategory.embedded,
                "Registration tied to an identifiable customer, plus a technical "
                "identifier — both PII per §16.5 when they point at a person.",
                "The car was AB12 CDE and I was logged in from 192.0.2.44 at the time.",
                [
                    (PIIKind.vehicle_registration, "AB12 CDE"),
                    (PIIKind.ip_address, "192.0.2.44"),
                ],
                preserves=["The car was", "and I was logged in from", "at the time."],
            ),
            self._fixture(
                "PII-EMB-07-international-format",
                PIICategory.embedded,
                "Same phone block in E.164 form — a redactor matching only the "
                "national format would miss this.",
                f"You can reach me on {DRAMA_PHONE_E164_PREFIX}789 — I'm abroad until Friday.",
                [(PIIKind.phone, f"{DRAMA_PHONE_E164_PREFIX}789")],
                preserves=["You can reach me on", "I'm abroad until Friday."],
            ),
        ]

    # -- edge cases: where naive redactors break -------------------------- #
    def edge_cases(self) -> list[RedactionFixture]:
        return [
            self._fixture(
                "PII-EDGE-01-adjacent-to-punctuation",
                PIICategory.embedded,
                "PII flush against punctuation — a token-boundary redactor either "
                "leaves a fragment or eats the punctuation.",
                "Email me (sam.patel@example.net), thanks!",
                [(PIIKind.email, "sam.patel@example.net")],
                preserves=["Email me (", "), thanks!"],
            ),
            self._fixture(
                "PII-EDGE-02-loyalty-looks-like-booking-ref",
                PIICategory.embedded,
                "Two similar-shaped identifiers in one line: they must be redacted "
                "as their own kinds, not collapsed into whichever pattern matches first.",
                "Loyalty HFB-LOY-1000001 and booking HFB-000789 are both on the account.",
                [
                    (PIIKind.loyalty_number, "HFB-LOY-1000001"),
                    (PIIKind.booking_reference, "HFB-000789"),
                ],
                preserves=["and booking", "are both on the account."],
            ),
            self._fixture(
                "PII-EDGE-03-no-pii-at-all",
                PIICategory.embedded,
                "The negative control: redaction must leave clean text untouched. "
                "Without this, an over-eager redactor looks correct on every fixture.",
                "Do you have an automatic estate available at Heathrow next Tuesday?",
                [],
                preserves=["Do you have an automatic estate available at Heathrow next Tuesday?"],
            ),
        ]
