"""PII query guardrail + lightweight redaction for a budgeting chat app."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?:\+1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})")
CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
ACCOUNT_RE = re.compile(r"\b(?:acct|account|routing|iban|swift|card)\s*[:#-]?\s*[A-Za-z0-9*_-]{4,}\b", re.I)
ADDRESS_HINT_RE = re.compile(r"\b\d{1,6}\s+[A-Za-z0-9.\- ]+\s(?:st|street|ave|avenue|rd|road|blvd|lane|ln|dr|drive|apt)\b", re.I)
NAME_FIELD_RE = re.compile(r"\b(name|customer|ship to|bill to)\s*[:\-]\s*[A-Za-z][A-Za-z ,.'-]{2,}", re.I)

# Intent-focused block terms for privacy attacks.
PII_INTENT_TERMS = {
    "ssn",
    "social security",
    "phone",
    "email",
    "address",
    "card number",
    "account number",
    "routing number",
    "student id",
    "real name",
    "full name",
    "who is person",
    "unredact",
    "de-anonymize",
    "deanonymize",
    "reveal identity",
}


@dataclass
class GuardrailResult:
    allowed: bool
    reason: str | None
    redacted_question: str
    matched_rules: List[str]


@dataclass
class DocumentRedactionResult:
    raw_text: str
    redacted_text: str
    pii_matches: List[str]


def redact_inline_pii(text: str) -> str:
    redacted = text
    redacted = NAME_FIELD_RE.sub("[REDACTED_NAME_FIELD]", redacted)
    redacted = SSN_RE.sub("[REDACTED_SSN]", redacted)
    redacted = EMAIL_RE.sub("[REDACTED_EMAIL]", redacted)
    redacted = PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    redacted = CARD_RE.sub("[REDACTED_CARD]", redacted)
    redacted = ACCOUNT_RE.sub("[REDACTED_ACCOUNT]", redacted)
    redacted = ADDRESS_HINT_RE.sub("[REDACTED_ADDRESS]", redacted)
    return redacted


def _detect_pii_patterns(text: str) -> List[str]:
    hits: List[str] = []
    if SSN_RE.search(text):
        hits.append("ssn_pattern")
    if EMAIL_RE.search(text):
        hits.append("email_pattern")
    if PHONE_RE.search(text):
        hits.append("phone_pattern")
    if CARD_RE.search(text):
        hits.append("card_pattern")
    if ACCOUNT_RE.search(text):
        hits.append("account_pattern")
    if ADDRESS_HINT_RE.search(text):
        hits.append("address_pattern")
    if NAME_FIELD_RE.search(text):
        hits.append("name_field_pattern")
    return hits


def detect_pii_patterns(text: str) -> List[str]:
    return _detect_pii_patterns(text)


def redact_document_text(text: str) -> DocumentRedactionResult:
    matches = detect_pii_patterns(text)
    redacted = redact_inline_pii(text)
    return DocumentRedactionResult(raw_text=text, redacted_text=redacted, pii_matches=matches)


def _detect_pii_intent(text: str) -> List[str]:
    lowered = text.lower()
    return [f"intent:{term}" for term in PII_INTENT_TERMS if term in lowered]


def guard_user_question(question: str) -> GuardrailResult:
    stripped = question.strip()
    redacted = redact_inline_pii(stripped)

    pattern_hits = _detect_pii_patterns(stripped)
    intent_hits = _detect_pii_intent(stripped)
    all_hits = pattern_hits + intent_hits

    if all_hits:
        return GuardrailResult(
            allowed=False,
            reason=(
                "I can't help with personal identifying information. "
                "I can still help with budgets, spending, debts, due dates, and savings insights."
            ),
            redacted_question=redacted,
            matched_rules=all_hits,
        )

    return GuardrailResult(allowed=True, reason=None, redacted_question=redacted, matched_rules=[])
