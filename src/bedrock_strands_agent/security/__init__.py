"""Security helpers — redaction and span-attribute safety.

Currently exposes :func:`redact_for_logs` for masking US PII patterns in
log records, span attributes, and eval artifacts. See ``redaction.py`` for
the hard rule on what *not* to redact (document_text on the model path).
"""

from bedrock_strands_agent.security.redaction import redact_for_logs

__all__ = ["redact_for_logs"]
