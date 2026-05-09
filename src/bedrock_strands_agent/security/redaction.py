r"""US-only PII redaction for logs, span attributes, and eval artifacts.

HARD RULE: never call :func:`redact_for_logs` on ``document_text`` *before*
model invocation — the model needs the raw document to extract correctly.
The helper is for downstream telemetry only:

- structured log records (``logging._RedactionFilter`` in Phase D4)
- span attributes copied from request data (Phase D1)
- eval-result JSON written to S3 (Phase D5/D9)
- payload sent to the online judge (Phase D8)

US patterns covered:

| Token                   | Pattern                                                           |
| ----------------------- | ----------------------------------------------------------------- |
| ``[REDACTED-KEY]``      | AWS access keys (``AKIA``/``ASIA``) and ``sk_``/``pk_`` API keys |
| ``[REDACTED-SSN]``      | ``\\d{3}-\\d{2}-\\d{4}``                                         |
| ``[REDACTED-EIN]``      | ``\\d{2}-\\d{7}``                                                |
| ``[REDACTED-EMAIL]``    | ``\\b[\\w.+-]+@[\\w-]+\\.[\\w.-]+\\b``                           |
| ``[REDACTED-PHONE]``    | US 10-digit phone with optional ``+1`` / parens / separators     |
| ``[REDACTED-CC]``       | 13-19 digit credit-card-shaped sequences                         |

Order matters: API key → SSN → EIN → email → phone → CC. SSN/EIN are checked
before the broader credit-card regex so a 9-digit SSN never gets swallowed
by the CC pattern.
"""

from __future__ import annotations

import re
from typing import Final

_API_KEY_RE: Final = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16,}\b|\b(?:sk|pk)_[A-Za-z0-9_]{16,}\b")
_SSN_RE: Final = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EIN_RE: Final = re.compile(r"\b\d{2}-\d{7}\b")
_EMAIL_RE: Final = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_US_PHONE_RE: Final = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")
_CC_RE: Final = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

_PATTERNS: Final = (
    (_API_KEY_RE, "[REDACTED-KEY]"),
    (_SSN_RE, "[REDACTED-SSN]"),
    (_EIN_RE, "[REDACTED-EIN]"),
    (_EMAIL_RE, "[REDACTED-EMAIL]"),
    (_US_PHONE_RE, "[REDACTED-PHONE]"),
    (_CC_RE, "[REDACTED-CC]"),
)


def redact_for_logs(text: str | None) -> str | None:
    """Mask US PII patterns in ``text``.

    Returns ``text`` unchanged when it is ``None`` or contains no matches.
    Safe to call on values of unknown provenance — :class:`None` is preserved.
    """
    if text is None:
        return None
    out = text
    for pattern, token in _PATTERNS:
        out = pattern.sub(token, out)
    return out
