"""Boto3-layer e2e test for the BedrockModel <-> bedrock-runtime wiring.

The rest of the suite mocks Strands at the ``Agent.__call__`` layer (see
``stub_extraction_service`` in ``tests/conftest.py``), which deliberately
hides whether ``BedrockModel`` is sending the right ``modelId`` and request
shape to ``bedrock-runtime``. This test fills that gap by:

1. Building a real ``AgentBundle`` via ``build_agent(settings)`` so a real
   ``strands.models.bedrock.BedrockModel`` is constructed (and with it, a
   real boto3 ``bedrock-runtime`` client).
2. Patching ``botocore.client.BaseClient._make_api_call`` to intercept the
   ``ConverseStream`` call (Strands defaults to streaming=True; see
   ``BedrockModel._stream`` in ``strands/models/bedrock.py``) and return a
   canned event-stream that decodes to the same JSON the
   ``stub_extraction_service`` fixture produces.
3. Driving a happy-path ``POST /extract`` via ``TestClient``.
4. Asserting the response body **and** that the captured ``api_params``
   carry the configured ``modelId`` — that's the regression catch the
   higher-layer mocks miss.

Why patch ``_make_api_call`` instead of ``botocore.stub.Stubber``: ``Stubber``
does not natively support event-stream responses (the ``stream`` key in the
``ConverseStream`` parsed response is an iterator that boto3 hydrates from a
live HTTP response). ``_make_api_call`` returns a plain dict, so we can hand
back ``{"stream": [...chunks...]}`` directly and the rest of the SDK consumes
it transparently.

This test runs unconditionally (no marker / opt-in env var): the patch
replaces *every* boto3 client call inside the test, so no AWS network or
credential resolution happens. The autouse ``_isolated_env`` fixture in
``tests/conftest.py`` already strips ``AWS_*`` env vars per-test, so even if
the developer's shell has real creds, none of them reach this test.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from botocore.client import BaseClient
from fastapi.testclient import TestClient

from bedrock_strands_agent.api.app import create_app
from bedrock_strands_agent.config import Settings

# The same canned payload shape used by ``stub_extraction_service`` so the
# resulting ``ExtractionResult`` is byte-for-byte equivalent to the existing
# happy-path tests — only the layer at which we mock differs.
_CANNED_EXTRACTION_JSON = (
    '{"fields": ['
    '{"name": "invoice_number", "value": "INV-001", "confidence": 0.95, '
    '"source_excerpt": "INVOICE #INV-001"},'
    '{"name": "invoice_date", "value": "2026-04-15", "confidence": 0.9, '
    '"source_excerpt": "Date"},'
    '{"name": "vendor_name", "value": "Acme", "confidence": 0.9, '
    '"source_excerpt": "Acme"},'
    '{"name": "bill_to", "value": "Wile E. Coyote", "confidence": 0.85, '
    '"source_excerpt": "Bill to"},'
    '{"name": "total", "value": 100, "confidence": 0.95, '
    '"source_excerpt": "Total"}'
    "]}"
)


def _converse_stream_chunks(text: str) -> Iterator[dict[str, Any]]:
    """Yield the minimal Bedrock Converse event-stream events Strands consumes.

    Mirrors the events handled by
    ``strands.event_loop.streaming.process_stream`` and the chunk dispatch in
    ``BedrockModel._stream``. The ``contentBlockDelta.delta.text`` field is
    what eventually populates ``AgentResult.message.content[0]['text']``,
    which the extraction service stringifies via ``str(result)``.
    """
    yield {"messageStart": {"role": "assistant"}}
    yield {"contentBlockDelta": {"delta": {"text": text}, "contentBlockIndex": 0}}
    yield {"contentBlockStop": {"contentBlockIndex": 0}}
    yield {"messageStop": {"stopReason": "end_turn"}}
    yield {
        "metadata": {
            "usage": {"inputTokens": 100, "outputTokens": 50, "totalTokens": 150},
            "metrics": {"latencyMs": 100},
        }
    }


def test_extract_e2e_through_real_bedrock_model_with_boto3_stub(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end /extract with a real BedrockModel, boto3 mocked at _make_api_call.

    This catches regressions in:
      - The model_id wiring from Settings -> BedrockModel -> bedrock-runtime
      - The region wiring (no real client construction must escape to AWS)
      - The request shape Strands builds (we assert ``modelId`` is present)
      - The response-parsing path (canned chunks must round-trip into a
        valid ``ExtractionResult``).
    """
    # The autouse ``_isolated_env`` fixture clears AWS_* per-test, but
    # ``boto3.Session()`` still resolves a default region from the
    # configuration loader. Set a deterministic region inside the test so the
    # client construction doesn't try (and fail) to read ~/.aws/config in CI.
    monkeypatch.setenv("AWS_DEFAULT_REGION", settings.aws_region)

    captured_calls: list[tuple[str, dict[str, Any]]] = []
    real_make_api_call = BaseClient._make_api_call

    def fake_make_api_call(
        self: BaseClient,
        operation_name: str,
        api_params: dict[str, Any],
    ) -> dict[str, Any]:
        # Identify the bedrock-runtime client. ``self.meta.service_model.service_name``
        # is the canonical hyphenated boto3 service name (e.g. "bedrock-runtime").
        service_name = self.meta.service_model.service_name
        if service_name != "bedrock-runtime":
            # Defensive: defer to the real implementation for any other
            # accidental client (none expected on this code path).
            return real_make_api_call(self, operation_name, api_params)  # pragma: no cover

        captured_calls.append((operation_name, api_params))
        if operation_name == "ConverseStream":
            return {
                "stream": _converse_stream_chunks(_CANNED_EXTRACTION_JSON),
                "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "stubbed-req-id"},
            }
        if operation_name == "Converse":  # pragma: no cover - streaming default
            return {
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [{"text": _CANNED_EXTRACTION_JSON}],
                    }
                },
                "stopReason": "end_turn",
                "usage": {"inputTokens": 100, "outputTokens": 50, "totalTokens": 150},
                "metrics": {"latencyMs": 100},
                "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "stubbed-req-id"},
            }
        msg = f"unexpected bedrock-runtime operation in stubbed test: {operation_name}"
        raise AssertionError(msg)

    monkeypatch.setattr(BaseClient, "_make_api_call", fake_make_api_call)

    # NOTE: pass settings only — no ``extraction_service`` override, so
    # ``create_app`` runs ``ExtractionService.from_settings(settings)``,
    # which goes through ``build_agent`` and constructs a real BedrockModel.
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": "doc"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schema"] == "invoice"
    assert body["model_id"] == settings.bedrock_model_id
    by_name = {f["name"]: f["value"] for f in body["fields"]}
    assert by_name["invoice_number"] == "INV-001"
    assert by_name["vendor_name"] == "Acme"

    # Regression-catching assertions: at least one ConverseStream call must
    # have hit the boto3 layer with the configured modelId. This is the wiring
    # that the agent-level mocks in the rest of the suite cannot validate.
    converse_calls = [
        params for op, params in captured_calls if op in ("ConverseStream", "Converse")
    ]
    assert converse_calls, (
        f"Expected at least one Converse/ConverseStream call to bedrock-runtime; "
        f"got operations: {[op for op, _ in captured_calls]}"
    )
    first_request = converse_calls[0]
    assert first_request.get("modelId") == settings.bedrock_model_id, (
        f"BedrockModel sent wrong modelId to bedrock-runtime: "
        f"expected {settings.bedrock_model_id!r}, got {first_request.get('modelId')!r}"
    )
    # Sanity: the request must include a non-empty messages list (the user prompt).
    messages = first_request.get("messages")
    assert isinstance(messages, list), f"Converse request missing 'messages': {first_request!r}"
    assert messages, f"Converse request has empty 'messages': {first_request!r}"
