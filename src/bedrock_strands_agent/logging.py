"""Structured-logging configuration.

JSON output by default with a correlation-id `ContextVar` injected into every
record, plus an always-on PII redaction filter. Idempotent: calling
`configure_logging` twice is safe.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Final

from pythonjsonlogger.json import JsonFormatter

from bedrock_strands_agent.config import Settings
from bedrock_strands_agent.security.redaction import redact_for_logs

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_NOISY_LIBS = ("botocore", "urllib3", "boto3", "httpx", "httpcore")

# Standard `LogRecord` attributes we MUST NOT touch — they are part of
# the logging framework's contract (e.g., `record.name`, `record.levelname`,
# `record.pathname`). The redaction filter walks string-valued attributes
# *not* in this set so that `extra={"foo": "..."}` user data is masked
# while framework metadata is left alone.
_RESERVED_RECORD_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        # Custom but framework-managed attributes set by `_ContextFilter`
        # and the JsonFormatter; redacting these would mangle output.
        "correlation_id",
        "asctime",
        "message",
    }
)


def set_correlation_id(value: str | None) -> None:
    """Set (or clear) the correlation id for the current context."""
    _correlation_id.set(value)


def get_correlation_id() -> str | None:
    """Return the correlation id for the current context, if any."""
    return _correlation_id.get()


class _ContextFilter(logging.Filter):
    """Attach the correlation id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Always returns True; mutates `record` in place."""
        record.correlation_id = _correlation_id.get()
        return True


class _RedactionFilter(logging.Filter):
    """Mask US PII patterns on every log record before it reaches a formatter.

    Defence-in-depth on top of the semgrep `no-print-of-document-text` rule
    and the manual span-attribute discipline enforced by the
    ``security-reviewer`` subagent. If a future code path accidentally
    routes a ``document_text`` value (or an ``ExtractedField.value``)
    through ``LOGGER.debug("...%s...", value)`` or
    ``LOGGER.info("foo", extra={"k": value})``, this filter rewrites
    known PII tokens (SSN, EIN, email, US phone, CC, AWS / sk_/pk_ API
    keys) to their redaction placeholders before the JSON or text
    formatter runs.

    Two surfaces are covered:

    * ``record.msg`` — the formatted message (after %-arg interpolation).
    * Any non-framework attribute on ``record`` that happens to be a
      string. This is the path through ``extra={...}`` that semgrep
      cannot statically see.

    The filter does NOT recurse into nested dict/list values; if a future
    incident shows extras carrying nested PII (e.g.,
    ``extra={"audit": {"ssn": "..."}}``), extend the walker. See ADR-0011.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Mutate ``record`` in place and always return True."""
        if record.args:
            # Interpolate %-args once so we can redact the rendered message.
            try:
                record.msg = record.getMessage()
            except Exception:  # pragma: no cover - pathological % formatting
                # Don't kill the log line on a formatting bug.
                return True
            record.args = ()
        if isinstance(record.msg, str):
            redacted = redact_for_logs(record.msg)
            if redacted is not None:
                record.msg = redacted
        for attr, value in list(record.__dict__.items()):
            if attr in _RESERVED_RECORD_ATTRS or attr.startswith("_"):
                continue
            if isinstance(value, str):
                masked = redact_for_logs(value)
                if masked is not None and masked != value:
                    record.__dict__[attr] = masked
        return True


def _build_handler(log_format: str) -> logging.Handler:
    handler = logging.StreamHandler(stream=sys.stdout)
    if log_format == "json":
        handler.setFormatter(
            JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s %(correlation_id)s",
                rename_fields={"asctime": "timestamp", "levelname": "level"},
            )
        )
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s %(name)s [%(correlation_id)s] %(message)s"
            )
        )
    # Filter order: redact first, then attach correlation_id (the id
    # itself is never sensitive — UUID4 — and we don't want it accidentally
    # rewritten).
    handler.addFilter(_RedactionFilter())
    handler.addFilter(_ContextFilter())
    return handler


def configure_logging(settings: Settings) -> None:
    """Configure the root logger from `settings`. Idempotent."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(settings.log_level)
    root.addHandler(_build_handler(settings.log_format))
    for name in _NOISY_LIBS:
        logging.getLogger(name).setLevel(logging.WARNING)
