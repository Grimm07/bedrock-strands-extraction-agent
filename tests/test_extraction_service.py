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
