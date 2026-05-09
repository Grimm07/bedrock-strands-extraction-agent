"""Tests for :func:`bedrock_strands_agent.security.redaction.redact_for_logs`
and the logging-side `_RedactionFilter`.
"""

from __future__ import annotations

import io
import logging

import pytest

from bedrock_strands_agent.config import Settings
from bedrock_strands_agent.logging import configure_logging
from bedrock_strands_agent.security.redaction import redact_for_logs


def _capture_logs(settings: Settings) -> tuple[logging.Logger, io.StringIO]:
    """Configure logging via `configure_logging` and capture stdout into a buffer.

    `configure_logging` clears prior handlers and installs a fresh one with
    `_RedactionFilter` + `_ContextFilter` attached; we re-aim the handler's
    stream at a `StringIO` so the test can inspect what would have been written.
    """
    configure_logging(settings)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    buf = io.StringIO()
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler):
            h.stream = buf
    return logging.getLogger("test.redaction.filter"), buf


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


# --------------------------------------------------------------------------- #
# Logging-side _RedactionFilter integration
# --------------------------------------------------------------------------- #


def test_redaction_filter_masks_pii_in_format_args(settings: Settings) -> None:
    """`LOGGER.info("...%s...", value)` is the most common PII-leak path.

    The filter must redact AFTER %-arg interpolation. Without this defence,
    a `LOGGER.debug("processing %s", document_text)` call with `LOG_LEVEL=DEBUG`
    enabled in prod would emit raw PII to stdout.
    """
    logger, buf = _capture_logs(settings)
    logger.info("seen ssn=%s in document body", "123-45-6789")
    out = buf.getvalue()
    assert "[REDACTED-SSN]" in out
    assert "123-45-6789" not in out


def test_redaction_filter_masks_pii_in_extra_dict(settings: Settings) -> None:
    """The `extra={...}` path is exactly what semgrep can NOT statically see.

    Semgrep blocks `LOGGER.*document_text` direct calls but not indirect
    routing through `extra={"k": document_text}`. This test pins that the
    filter walks every non-framework string attribute on the record. The
    JSON formatter (which is the production default) serialises all extras
    including unknown keys, so the leak surfaces there even when the text
    format would silently drop them.
    """
    json_settings = settings.model_copy(update={"log_format": "json"})
    logger, buf = _capture_logs(json_settings)
    logger.info("audit", extra={"document_excerpt": "contact alice@example.com"})
    out = buf.getvalue()
    assert "[REDACTED-EMAIL]" in out
    assert "alice@example.com" not in out


def test_redaction_filter_does_not_touch_correlation_id(settings: Settings) -> None:
    """Correlation IDs are UUIDs, never PII, but they look like arbitrary strings.

    The filter's reserved-attribute set must keep `correlation_id` untouched
    so log-correlation across services keeps working.
    """
    from bedrock_strands_agent.logging import set_correlation_id

    set_correlation_id("11111111-2222-3333-4444-555555555555")
    try:
        logger, buf = _capture_logs(settings)
        logger.info("hello")
        out = buf.getvalue()
        assert "11111111-2222-3333-4444-555555555555" in out
    finally:
        set_correlation_id(None)


def test_redaction_filter_passes_benign_messages_through(settings: Settings) -> None:
    """Messages without PII tokens must round-trip unchanged.

    Guard against false positives that would pollute ordinary operational
    logs with `[REDACTED-*]` placeholders.
    """
    logger, buf = _capture_logs(settings)
    logger.warning("retrying bedrock call attempt %d", 2)
    out = buf.getvalue()
    assert "retrying bedrock call attempt 2" in out
    assert "[REDACTED-" not in out
