"""Tests for :func:`bedrock_strands_agent.security.redaction.redact_for_logs`."""

from __future__ import annotations

import pytest

from bedrock_strands_agent.security.redaction import redact_for_logs


@pytest.mark.parametrize(
    ("raw", "expected_token", "leaked"),
    [
        ("SSN: 123-45-6789 in record", "[REDACTED-SSN]", "123-45-6789"),
        ("EIN: 12-3456789 today", "[REDACTED-EIN]", "12-3456789"),
        (
            "contact me at jane.doe+tag@example.com asap",
            "[REDACTED-EMAIL]",
            "jane.doe+tag@example.com",
        ),
        ("call (555) 123-4567 today", "[REDACTED-PHONE]", "(555) 123-4567"),
        ("call +1 555-123-4567 today", "[REDACTED-PHONE]", "555-123-4567"),
        ("call 555.123.4567 today", "[REDACTED-PHONE]", "555.123.4567"),
        ("card 4111-1111-1111-1111 expired", "[REDACTED-CC]", "4111-1111-1111-1111"),
        ("AKIAEXAMPLEKEY1234567 is the key", "[REDACTED-KEY]", "AKIAEXAMPLEKEY1234567"),
        (
            "token sk_live_abcdef1234567890XYZ found",
            "[REDACTED-KEY]",
            "sk_live_abcdef1234567890XYZ",
        ),
    ],
)
def test_pattern_is_masked(raw: str, expected_token: str, leaked: str) -> None:
    out = redact_for_logs(raw)
    assert out is not None
    assert expected_token in out
    assert leaked not in out


def test_returns_none_for_none_input() -> None:
    assert redact_for_logs(None) is None


def test_passes_benign_text_unchanged() -> None:
    raw = "the quick brown fox jumps over the lazy dog"
    assert redact_for_logs(raw) == raw


def test_multiple_patterns_in_one_string() -> None:
    raw = "user jane@example.com SSN 123-45-6789 phone (555) 123-4567"
    out = redact_for_logs(raw) or ""
    assert "[REDACTED-EMAIL]" in out
    assert "[REDACTED-SSN]" in out
    assert "[REDACTED-PHONE]" in out
    assert "jane@example.com" not in out
    assert "123-45-6789" not in out


def test_ssn_takes_precedence_over_credit_card() -> None:
    """An SSN-shaped value should be tagged SSN, not CC. Order matters."""
    out = redact_for_logs("123-45-6789") or ""
    assert "[REDACTED-SSN]" in out
    assert "[REDACTED-CC]" not in out


def test_empty_string_returns_empty_string() -> None:
    assert redact_for_logs("") == ""
