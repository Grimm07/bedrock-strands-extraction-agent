"""Structured-logging configuration.

JSON output by default with a correlation-id `ContextVar` injected into every
record. Idempotent: calling `configure_logging` twice is safe.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

from pythonjsonlogger.json import JsonFormatter

from bedrock_strands_agent.config import Settings

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_NOISY_LIBS = ("botocore", "urllib3", "boto3", "httpx", "httpcore")


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
