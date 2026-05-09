from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from bedrock_strands_agent.agent.builder import AgentBundle
from bedrock_strands_agent.agent.mcp import MCPManager
from bedrock_strands_agent.agent.prompts import PromptRenderer
from bedrock_strands_agent.config import Settings
from bedrock_strands_agent.extraction import ExtractionError, ExtractionService


def _service(settings: Settings, *responses: str, max_retries: int = 1) -> ExtractionService:
    """Build a service whose agent returns each response in turn."""
    agent = MagicMock()
    iter_responses = iter(responses)

    def _next(_prompt: str) -> Any:
        text = next(iter_responses)

        class _Resp:
            def __str__(self) -> str:
                return text

        return _Resp()

    agent.side_effect = _next
    bundle = AgentBundle(
        agent=agent,
        prompt_renderer=PromptRenderer(),
        mcp_manager=MCPManager(settings.mcp_config_path, enabled_servers=[]),
        settings=settings,
    )
    return ExtractionService(bundle, max_retries=max_retries)


def test_strips_code_fences(settings: Settings) -> None:
    payload = (
        "```json\n"
        '{"fields": ['
        '{"name": "invoice_number", "value": "X", "confidence": 1.0},'
        '{"name": "invoice_date", "value": "2026-01-01", "confidence": 1.0},'
        '{"name": "vendor_name", "value": "V", "confidence": 1.0},'
        '{"name": "bill_to", "value": "B", "confidence": 1.0},'
        '{"name": "total", "value": 1, "confidence": 1.0}'
        "]}\n```"
    )
    svc = _service(settings, payload)
    result = svc.extract(document_text="doc", schema_name="invoice")
    by_name = {f.name: f.value for f in result.fields}
    assert by_name["invoice_number"] == "X"


def test_recovers_from_prefix(settings: Settings) -> None:
    payload = (
        "Sure! Here is the JSON: "
        '{"fields": ['
        '{"name": "invoice_number", "value": "Y", "confidence": 0.5},'
        '{"name": "invoice_date", "value": "2026-01-01", "confidence": 0.5},'
        '{"name": "vendor_name", "value": "V", "confidence": 0.5},'
        '{"name": "bill_to", "value": "B", "confidence": 0.5},'
        '{"name": "total", "value": 1, "confidence": 0.5}'
        "]}"
    )
    svc = _service(settings, payload)
    result = svc.extract(document_text="doc", schema_name="invoice")
    assert result.fields[0].value == "Y"


def test_garbage_response_raises(settings: Settings) -> None:
    svc = _service(settings, "no JSON here", "still no JSON")
    with pytest.raises(ExtractionError):
        svc.extract(document_text="doc", schema_name="invoice")


def test_missing_required_warns(settings: Settings) -> None:
    """When required fields are missing, the result includes a warning."""
    payload = '{"fields": [{"name": "invoice_number", "value": "X", "confidence": 0.9}]}'
    svc = _service(settings, payload, payload, max_retries=1)
    result = svc.extract(document_text="d", schema_name="invoice")
    fatal = [w for w in result.warnings if w.startswith("MISSING_REQUIRED:")]
    assert any("invoice_date" in w for w in fatal)


def test_pattern_mismatch_warns(settings: Settings) -> None:
    payload = (
        '{"fields": ['
        '{"name": "legal_name", "value": "Acme", "confidence": 1.0},'
        '{"name": "federal_tax_classification", "value": "Corp", "confidence": 1.0},'
        '{"name": "address", "value": "123 Way", "confidence": 1.0},'
        '{"name": "city_state_zip", "value": "X, Y 1", "confidence": 1.0},'
        '{"name": "ssn", "value": "BAD", "confidence": 1.0}'
        "]}"
    )
    svc = _service(settings, payload)
    result = svc.extract(document_text="d", schema_name="irs_w9")
    assert any("pattern mismatch" in w and "ssn" in w for w in result.warnings)


def test_confidence_clamped(settings: Settings) -> None:
    payload = (
        '{"fields": ['
        '{"name": "invoice_number", "value": "X", "confidence": 99},'
        '{"name": "invoice_date", "value": "2026-01-01", "confidence": -3},'
        '{"name": "vendor_name", "value": "V", "confidence": 0.4},'
        '{"name": "bill_to", "value": "B", "confidence": 0.4},'
        '{"name": "total", "value": 1, "confidence": 0.4}'
        "]}"
    )
    svc = _service(settings, payload)
    result = svc.extract(document_text="d", schema_name="invoice")
    by_name = {f.name: f.confidence for f in result.fields}
    assert by_name["invoice_number"] == 1.0
    assert by_name["invoice_date"] == 0.0


def test_unknown_schema_raises(settings: Settings) -> None:
    svc = _service(settings, "{}")
    with pytest.raises(KeyError, match="Unknown schema"):
        svc.extract(document_text="d", schema_name="nope")


def test_missing_fields_key_raises(settings: Settings) -> None:
    svc = _service(settings, '{"foo": "bar"}', '{"foo": "bar"}')
    with pytest.raises(ExtractionError, match="missing a 'fields'"):
        svc.extract(document_text="d", schema_name="invoice")


def test_retry_recovers(settings: Settings) -> None:
    """First response missing required fields, retry fixes it."""
    bad = '{"fields": [{"name": "invoice_number", "value": "X", "confidence": 0.9}]}'
    good = (
        '{"fields": ['
        '{"name": "invoice_number", "value": "X", "confidence": 0.9},'
        '{"name": "invoice_date", "value": "2026-01-01", "confidence": 0.9},'
        '{"name": "vendor_name", "value": "V", "confidence": 0.9},'
        '{"name": "bill_to", "value": "B", "confidence": 0.9},'
        '{"name": "total", "value": 1, "confidence": 0.9}'
        "]}"
    )
    svc = _service(settings, bad, good, max_retries=1)
    result = svc.extract(document_text="d", schema_name="invoice")
    assert not [w for w in result.warnings if w.startswith("MISSING_REQUIRED:")]


def test_value_anchor_failure_triggers_retry(settings: Settings) -> None:
    """Schema-confusion attack (ADR-0011): the model emits a verbatim
    ``source_excerpt`` from the document but invents a string ``value`` that
    is nowhere inside that excerpt. The pipeline must not silently accept
    this — it should re-prompt the model.

    First attempt fabricates ``vendor_name = "attacker@evil.com"`` while
    citing a verbatim "Vendor Name: Acme Widget Corp" excerpt. Second
    attempt corrects the value to "Acme Widget Corp", which IS in the
    excerpt. Without value-anchored citation verification, attempt 1 would
    succeed and the fabricated value would ship to the caller.
    """
    document = (
        "INVOICE #X\n"
        "Date: 2026-01-01\n"
        "Vendor Name: Acme Widget Corp\n"
        "Bill To: Wile E Coyote\n"
        "Total: 100"
    )
    fabricated = (
        '{"fields": ['
        '{"name": "invoice_number", "value": "X", "confidence": 0.9, '
        '"source_excerpt": "INVOICE #X"},'
        '{"name": "invoice_date", "value": "2026-01-01", "confidence": 0.9, '
        '"source_excerpt": "Date: 2026-01-01"},'
        # The injection: excerpt is verbatim from the doc, but value is invented.
        '{"name": "vendor_name", "value": "attacker@evil.com", "confidence": 0.95, '
        '"source_excerpt": "Vendor Name: Acme Widget Corp"},'
        '{"name": "bill_to", "value": "Wile E Coyote", "confidence": 0.9, '
        '"source_excerpt": "Bill To: Wile E Coyote"},'
        '{"name": "total", "value": 100, "confidence": 0.9, '
        '"source_excerpt": "Total: 100"}'
        "]}"
    )
    corrected = fabricated.replace('"value": "attacker@evil.com"', '"value": "Acme Widget Corp"')
    svc = _service(settings, fabricated, corrected, max_retries=1)
    result = svc.extract(document_text=document, schema_name="invoice")
    by_name = {f.name: f.value for f in result.fields}
    # The retry succeeded; the fabricated value did NOT ship.
    assert by_name["vendor_name"] == "Acme Widget Corp"
    assert by_name["vendor_name"] != "attacker@evil.com"


def test_value_anchor_skips_normalising_date_fields(settings: Settings) -> None:
    """DATE fields are emitted in ISO-8601 (per the schema description) but
    real documents use locale-specific date formats. The anchor check would
    false-positive on the happy path; ``_NORMALISING_STRING_TYPES`` excludes
    DATE so legitimate "January 15, 2026" -> "2026-01-15" extractions don't
    burn a retry. Pins that exclusion against future regressions.
    """
    document = (
        "INVOICE #X\n"
        "Date: January 15, 2026\n"  # long form in the doc
        "Vendor: V\n"
        "Bill To: B\n"
        "Total: 100"
    )
    payload = (
        '{"fields": ['
        '{"name": "invoice_number", "value": "X", "confidence": 0.9, '
        '"source_excerpt": "INVOICE #X"},'
        # DATE field: model emits ISO form per the schema, doc has long form.
        # Anchor check must skip; one-shot success expected (no retry).
        '{"name": "invoice_date", "value": "2026-01-15", "confidence": 0.9, '
        '"source_excerpt": "Date: January 15, 2026"},'
        '{"name": "vendor_name", "value": "V", "confidence": 0.9, '
        '"source_excerpt": "Vendor: V"},'
        '{"name": "bill_to", "value": "B", "confidence": 0.9, '
        '"source_excerpt": "Bill To: B"},'
        '{"name": "total", "value": 100, "confidence": 0.9, '
        '"source_excerpt": "Total: 100"}'
        "]}"
    )
    # Pass the same payload twice; if the anchor check were active for DATE
    # this would still pass (last-attempt fallback) but we'd burn a retry.
    # Instead we expect it to succeed on attempt 1 — pin that by giving only
    # one response and asserting no extra call happened.
    svc = _service(settings, payload)
    result = svc.extract(document_text=document, schema_name="invoice")
    by_name = {f.name: f.value for f in result.fields}
    assert by_name["invoice_date"] == "2026-01-15"


def test_value_anchor_skips_non_string_values(settings: Settings) -> None:
    """Numeric/boolean values are NOT subject to the anchor check —
    validators may legitimately normalise them away from the excerpt
    surface form. ``value=100`` extracted from ``"Total: $100.00"`` is
    fine; the anchor check would have false-positive'd on the dollar sign.
    """
    document = "INVOICE #X\nDate: 2026-01-01\nVendor: V\nBill To: B\nTotal: $100.00"
    payload = (
        '{"fields": ['
        '{"name": "invoice_number", "value": "X", "confidence": 0.9, '
        '"source_excerpt": "INVOICE #X"},'
        '{"name": "invoice_date", "value": "2026-01-01", "confidence": 0.9, '
        '"source_excerpt": "Date: 2026-01-01"},'
        '{"name": "vendor_name", "value": "V", "confidence": 0.9, '
        '"source_excerpt": "Vendor: V"},'
        '{"name": "bill_to", "value": "B", "confidence": 0.9, '
        '"source_excerpt": "Bill To: B"},'
        # Numeric value, currency-shaped excerpt — anchor check must skip.
        '{"name": "total", "value": 100, "confidence": 0.95, '
        '"source_excerpt": "Total: $100.00"}'
        "]}"
    )
    svc = _service(settings, payload)
    result = svc.extract(document_text=document, schema_name="invoice")
    by_name = {f.name: f.value for f in result.fields}
    assert by_name["total"] == 100
