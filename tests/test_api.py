from __future__ import annotations

from fastapi.testclient import TestClient

from bedrock_strands_agent.api.app import create_app
from bedrock_strands_agent.config import Settings
from bedrock_strands_agent.extraction import ExtractionService
from bedrock_strands_agent.extraction.schemas import get_schema


def _client(settings: Settings, service: ExtractionService) -> TestClient:
    app = create_app(settings, extraction_service=service)
    return TestClient(app)


def test_health(settings: Settings, stub_extraction_service: ExtractionService) -> None:
    with _client(settings, stub_extraction_service) as c:
        r = c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == settings.service_name


def test_schemas(settings: Settings, stub_extraction_service: ExtractionService) -> None:
    with _client(settings, stub_extraction_service) as c:
        r = c.get("/schemas")
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["schemas"]]
    assert "irs_w9" in names
    assert "invoice" in names


def test_extract_success(settings: Settings, stub_extraction_service: ExtractionService) -> None:
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": "doc"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"] == "invoice"
    assert body["fields"][0]["name"] == "invoice_number"


def test_extract_unknown_schema(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={"schema_name": "nope", "document_text": "doc"},
        )
    assert r.status_code == 404


def test_extract_with_matching_schema_version_succeeds(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """Pinning to the registered version is a no-op pass-through."""

    current_version = get_schema("invoice").version
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={
                "schema_name": "invoice",
                "schema_version": current_version,
                "document_text": "doc",
            },
        )
    assert r.status_code == 200, r.text


def test_extract_with_mismatched_schema_version_returns_404(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """Pinning to a version that doesn't match the registry yields 404."""
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={
                "schema_name": "invoice",
                "schema_version": "9.9.9-no-such",
                "document_text": "doc",
            },
        )
    assert r.status_code == 404
    assert "9.9.9-no-such" in r.json()["detail"]


def test_extract_empty_text(settings: Settings, stub_extraction_service: ExtractionService) -> None:
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": ""},
        )
    assert r.status_code == 422


def test_extract_oversize_document_text_returns_422(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """Document text exceeding `max_length` is rejected at the API layer.

    Bedrock's context-window limit would catch oversized payloads anyway, but
    rejecting at the API saves a model call and bounds memory cost on the
    threadpool worker. See the prompt-injection threat-model ADR.
    """
    huge = "a" * 200_001  # one character past the documented 200,000-char cap
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": huge},
        )
    assert r.status_code == 422
    # Pydantic surfaces this as a string-too-long error in the validation
    # detail; the exact wording depends on the FastAPI/pydantic versions but
    # the field name is stable.
    body = r.json()
    detail_blob = str(body)
    assert "document_text" in detail_blob


def test_correlation_id_round_trip(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    with _client(settings, stub_extraction_service) as c:
        r = c.get("/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


def test_correlation_id_generated(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    with _client(settings, stub_extraction_service) as c:
        r = c.get("/health")
    assert r.headers.get("x-request-id")


def test_metrics_exposed(settings: Settings, stub_extraction_service: ExtractionService) -> None:
    with _client(settings, stub_extraction_service) as c:
        c.get("/health")
        r = c.get("/metrics")
    assert r.status_code == 200
    assert "http_request" in r.text or "process_" in r.text


# --------------------------------------------------------------------------- #
# /extract/document
# --------------------------------------------------------------------------- #
import io as _io  # noqa: E402  - keep test imports localised to the relevant block

import pytest  # noqa: E402
from PIL import Image as _PILImage  # noqa: E402


def _png_upload_bytes() -> bytes:
    buf = _io.BytesIO()
    _PILImage.new("RGB", (32, 32), color="red").save(buf, format="PNG")
    return buf.getvalue()


_INVOICE_CANNED = (
    '{"fields": ['
    '{"name": "invoice_number", "value": "INV-9", "confidence": 0.95},'
    '{"name": "invoice_date", "value": "2026-04-15", "confidence": 0.9},'
    '{"name": "vendor_name", "value": "Acme", "confidence": 0.9},'
    '{"name": "bill_to", "value": "Wile E. Coyote", "confidence": 0.85},'
    '{"name": "total", "value": 100, "confidence": 0.95}'
    "]}"
)


def test_extract_document_image_routes_to_vision(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PNG upload exercises the vision path; we mock invoke_multimodal."""
    monkeypatch.setattr(
        "bedrock_strands_agent.extraction.service.invoke_multimodal",
        lambda **_kw: _INVOICE_CANNED,
    )
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract/document",
            data={"schema_name": "invoice"},
            files={"file": ("test.png", _png_upload_bytes(), "image/png")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"] == "invoice"
    assert body["fields"][0]["name"] == "invoice_number"


def test_extract_document_pdf_with_text_routes_to_text(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PDF with embedded text falls through to the text path (uses the stub agent)."""

    class _Page:
        def extract_text(self) -> str:
            return "INVOICE #INV-001 Date: 2026-04-15"

    class _Reader:
        def __init__(self, *_a: object, **_kw: object) -> None:
            return

        @property
        def pages(self) -> list[_Page]:
            return [_Page()]

    monkeypatch.setattr("bedrock_strands_agent.extraction.document.PdfReader", _Reader)
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract/document",
            data={"schema_name": "invoice"},
            files={"file": ("test.pdf", b"%PDF-1.4 stub", "application/pdf")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"] == "invoice"


def _image_only_pdf_bytes(num_pages: int = 1) -> bytes:
    """Build an image-only PDF (no text layer) — surrogate for a scan."""
    palette = ("red", "green", "blue")
    pages = [
        _PILImage.new("RGB", (120, 160), color=palette[i % len(palette)]) for i in range(num_pages)
    ]
    buf = _io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:])
    return buf.getvalue()


def test_extract_document_scanned_pdf_routes_to_vision(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PDF without embedded text is rasterised server-side and routed to vision."""
    captured_image_count: dict[str, int] = {}

    def _fake_invoke_multimodal(**kwargs: object) -> str:
        images = kwargs.get("images")
        captured_image_count["count"] = len(list(images)) if images is not None else 0
        return _INVOICE_CANNED

    monkeypatch.setattr(
        "bedrock_strands_agent.extraction.service.invoke_multimodal",
        _fake_invoke_multimodal,
    )
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract/document",
            data={"schema_name": "invoice"},
            files={"file": ("scan.pdf", _image_only_pdf_bytes(num_pages=2), "application/pdf")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"] == "invoice"
    assert body["fields"][0]["name"] == "invoice_number"
    # Both pages must have been rasterised and forwarded to the vision call.
    assert captured_image_count["count"] == 2


def test_extract_document_vision_grounding_failure_triggers_retry(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0011 vision-mode grounding (Critical follow-on): when the
    second-pass model call flags a candidate field as NOT present in the
    image, the extraction loop must re-prompt the model. A successful
    retry ships the corrected value, not the fabricated original.

    Sequencing:
      1. invoke_multimodal call 1: model returns fabricated `vendor_name`.
      2. invoke_multimodal call 2: grounding verifier flags vendor_name
         as `present=false`.
      3. Loop kicks a retry.
      4. invoke_multimodal call 3: model returns corrected vendor_name.
      5. invoke_multimodal call 4: grounding verifier confirms all present.
      6. Result ships with the corrected value.
    """
    fabricated = (
        '{"fields": ['
        '{"name": "invoice_number", "value": "X", "confidence": 0.9, '
        '"source_excerpt": "INVOICE #X"},'
        '{"name": "invoice_date", "value": "2026-01-01", "confidence": 0.9, '
        '"source_excerpt": "Date: 2026-01-01"},'
        '{"name": "vendor_name", "value": "FAKE", "confidence": 0.9, '
        '"source_excerpt": "Vendor: FAKE"},'
        '{"name": "bill_to", "value": "B", "confidence": 0.9, '
        '"source_excerpt": "Bill To: B"},'
        '{"name": "total", "value": 100, "confidence": 0.9, '
        '"source_excerpt": "Total: 100"}'
        "]}"
    )
    grounding_flag_vendor = (
        '{"groundings": ['
        '{"name": "invoice_number", "present": true},'
        '{"name": "invoice_date", "present": true},'
        '{"name": "vendor_name", "present": false},'
        '{"name": "bill_to", "present": true},'
        '{"name": "total", "present": true}'
        "]}"
    )
    corrected = fabricated.replace('"value": "FAKE"', '"value": "Real Vendor"')
    grounding_all_present = grounding_flag_vendor.replace('"present": false', '"present": true')

    responses = iter([fabricated, grounding_flag_vendor, corrected, grounding_all_present])

    def _fake_invoke_multimodal(**_kw: object) -> str:
        return next(responses)

    monkeypatch.setattr(
        "bedrock_strands_agent.extraction.service.invoke_multimodal",
        _fake_invoke_multimodal,
    )
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract/document",
            data={"schema_name": "invoice"},
            files={"file": ("test.png", _png_upload_bytes(), "image/png")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    by_name = {f["name"]: f["value"] for f in body["fields"]}
    # The retry succeeded: the fabricated value did NOT ship.
    assert by_name["vendor_name"] == "Real Vendor"
    assert by_name["vendor_name"] != "FAKE"


def test_extract_document_grounding_transport_failure_fails_open(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A throttled / network-failed verifier (Bedrock ClientError) must NOT
    crash the whole extraction. The first-pass result still ships.

    Without this, a transient verifier outage 5xx's the whole request even
    though the extraction itself succeeded — the opposite of fail-open.
    """
    from botocore.exceptions import ClientError

    extraction_payload = (
        '{"fields": ['
        '{"name": "invoice_number", "value": "X", "confidence": 0.9, '
        '"source_excerpt": "INVOICE #X"},'
        '{"name": "invoice_date", "value": "2026-01-01", "confidence": 0.9, '
        '"source_excerpt": "Date"},'
        '{"name": "vendor_name", "value": "Acme", "confidence": 0.9, '
        '"source_excerpt": "Vendor"},'
        '{"name": "bill_to", "value": "B", "confidence": 0.9, '
        '"source_excerpt": "Bill To"},'
        '{"name": "total", "value": 100, "confidence": 0.9, '
        '"source_excerpt": "Total"}'
        "]}"
    )
    call_index = {"n": 0}

    def _fake(**_kw: object) -> str:
        call_index["n"] += 1
        if call_index["n"] == 1:
            return extraction_payload
        # Second call (the verifier) raises Bedrock ClientError to simulate
        # throttling-after-retry-exhaustion.
        raise ClientError(
            error_response={
                "Error": {"Code": "ThrottlingException"},
                "ResponseMetadata": {"HTTPStatusCode": 429},
            },
            operation_name="Converse",
        )

    monkeypatch.setattr("bedrock_strands_agent.extraction.service.invoke_multimodal", _fake)
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract/document",
            data={"schema_name": "invoice"},
            files={"file": ("test.png", _png_upload_bytes(), "image/png")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    by_name = {f["name"]: f["value"] for f in body["fields"]}
    assert by_name["vendor_name"] == "Acme"


def test_extract_document_grounding_failure_persists_yields_ungrounded_warning(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the verifier flags the same field on every retry, the loop must
    eventually ship the result (auto-no-human-review goal) but mark the
    field with an `UNGROUNDED:<name>` warning so downstream consumers can
    route those records to a different bucket. Without this the only
    visible signal is the trace span — fine for SREs, useless for callers.
    """
    fabricated = (
        '{"fields": ['
        '{"name": "invoice_number", "value": "X", "confidence": 0.9, '
        '"source_excerpt": "INVOICE #X"},'
        '{"name": "invoice_date", "value": "2026-01-01", "confidence": 0.9, '
        '"source_excerpt": "Date"},'
        '{"name": "vendor_name", "value": "FAKE", "confidence": 0.9, '
        '"source_excerpt": "V"},'
        '{"name": "bill_to", "value": "B", "confidence": 0.9, '
        '"source_excerpt": "Bill To"},'
        '{"name": "total", "value": 100, "confidence": 0.9, '
        '"source_excerpt": "Total"}'
        "]}"
    )
    grounding_flag_vendor = (
        '{"groundings": ['
        '{"name": "invoice_number", "present": true},'
        '{"name": "invoice_date", "present": true},'
        '{"name": "vendor_name", "present": false},'
        '{"name": "bill_to", "present": true},'
        '{"name": "total", "present": true}'
        "]}"
    )
    # 3 attempts x 2 calls each = 6 responses. Vendor grounding always fails.
    responses = iter(
        [
            fabricated,
            grounding_flag_vendor,
            fabricated,
            grounding_flag_vendor,
            fabricated,
            grounding_flag_vendor,
        ]
    )

    def _fake(**_kw: object) -> str:
        return next(responses)

    monkeypatch.setattr("bedrock_strands_agent.extraction.service.invoke_multimodal", _fake)
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract/document",
            data={"schema_name": "invoice"},
            files={"file": ("test.png", _png_upload_bytes(), "image/png")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    # Result ships (auto-no-human-review) but the per-field miss must be
    # surfaced in warnings so callers can route accordingly.
    assert "UNGROUNDED:vendor_name" in body["warnings"]


def test_extract_document_grounding_parse_failure_fails_open(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the second-pass grounding verifier returns malformed JSON, the
    extraction must NOT be invalidated. A verifier that can't speak the
    contract is treated as silent — the first-pass schema validation still
    constrains the result. Documented in `_verify_vision_grounding`.
    """
    extraction_payload = (
        '{"fields": ['
        '{"name": "invoice_number", "value": "X", "confidence": 0.9, '
        '"source_excerpt": "INVOICE #X"},'
        '{"name": "invoice_date", "value": "2026-01-01", "confidence": 0.9, '
        '"source_excerpt": "Date"},'
        '{"name": "vendor_name", "value": "Acme", "confidence": 0.9, '
        '"source_excerpt": "Vendor"},'
        '{"name": "bill_to", "value": "B", "confidence": 0.9, '
        '"source_excerpt": "Bill To"},'
        '{"name": "total", "value": 100, "confidence": 0.9, '
        '"source_excerpt": "Total"}'
        "]}"
    )
    responses = iter([extraction_payload, "this is not JSON at all"])

    def _fake(**_kw: object) -> str:
        return next(responses)

    monkeypatch.setattr("bedrock_strands_agent.extraction.service.invoke_multimodal", _fake)
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract/document",
            data={"schema_name": "invoice"},
            files={"file": ("test.png", _png_upload_bytes(), "image/png")},
        )
    # Fail-open: the extraction's first-pass result ships even when the
    # grounding verifier returns garbage.
    assert r.status_code == 200, r.text
    body = r.json()
    by_name = {f["name"]: f["value"] for f in body["fields"]}
    assert by_name["vendor_name"] == "Acme"


def test_extract_document_unsupported_mime_returns_422(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract/document",
            data={"schema_name": "invoice"},
            files={"file": ("test.html", b"<html/>", "text/html")},
        )
    assert r.status_code == 422
    assert "unsupported" in r.json()["detail"].lower()


def test_extract_document_with_mismatched_schema_version_returns_404(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """schema_version is also accepted (and pin-checked) as a multipart Form field."""
    monkeypatch.setattr(
        "bedrock_strands_agent.extraction.service.invoke_multimodal",
        lambda **_kw: _INVOICE_CANNED,
    )
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract/document",
            data={"schema_name": "invoice", "schema_version": "9.9.9-no-such"},
            files={"file": ("test.png", _png_upload_bytes(), "image/png")},
        )
    assert r.status_code == 404
    assert "9.9.9-no-such" in r.json()["detail"]


def test_extract_document_unknown_schema_returns_404(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "bedrock_strands_agent.extraction.service.invoke_multimodal",
        lambda **_kw: _INVOICE_CANNED,
    )
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract/document",
            data={"schema_name": "no_such_schema"},
            files={"file": ("test.png", _png_upload_bytes(), "image/png")},
        )
    assert r.status_code == 404


def test_extract_document_invalid_image_returns_422(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract/document",
            data={"schema_name": "invoice"},
            files={"file": ("test.png", b"definitely-not-a-png", "image/png")},
        )
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# OpenAPI documents typed error bodies
# --------------------------------------------------------------------------- #


def _err_ref(response_entry: dict[str, object]) -> str | None:
    """Return the $ref string for application/json on a response entry."""
    content = response_entry.get("content") if isinstance(response_entry, dict) else None
    if not isinstance(content, dict):
        return None
    media = content.get("application/json")
    if not isinstance(media, dict):
        return None
    schema = media.get("schema")
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    return ref if isinstance(ref, str) else None


def test_openapi_documents_error_responses_for_extract(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    with _client(settings, stub_extraction_service) as c:
        schema = c.get("/openapi.json").json()
    responses = schema["paths"]["/extract"]["post"]["responses"]
    assert "404" in responses
    assert "502" in responses
    assert _err_ref(responses["404"]) == "#/components/schemas/ErrorResponse"
    assert _err_ref(responses["502"]) == "#/components/schemas/ErrorResponse"


def test_openapi_documents_error_responses_for_extract_document(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    with _client(settings, stub_extraction_service) as c:
        schema = c.get("/openapi.json").json()
    responses = schema["paths"]["/extract/document"]["post"]["responses"]
    for code in ("404", "422", "502"):
        assert code in responses, f"missing {code} in {list(responses)}"
        assert _err_ref(responses[code]) == "#/components/schemas/ErrorResponse", (
            f"{code} response does not reference ErrorResponse"
        )


def test_openapi_documents_schema_version_as_optional_on_both_routes(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """`schema_version` is exposed on both routes and is never required."""
    with _client(settings, stub_extraction_service) as c:
        schema = c.get("/openapi.json").json()

    body_schemas = schema["components"]["schemas"]
    extract_body = body_schemas["ExtractRequestBody"]
    assert "schema_version" in extract_body["properties"]
    assert "schema_version" not in extract_body.get("required", [])

    # The multipart Form field for /extract/document is also exposed.
    doc_body_name = next(name for name in body_schemas if name.startswith("Body_extract_document"))
    doc_body = body_schemas[doc_body_name]
    assert "schema_version" in doc_body["properties"]
    assert "schema_version" not in doc_body.get("required", [])


# --------------------------------------------------------------------------- #
# /extract/stream — Server-Sent Events
# --------------------------------------------------------------------------- #


def _parse_sse(raw: str) -> list[tuple[str, str]]:
    """Split an SSE response body into a list of ``(event_type, data)`` tuples."""
    frames = []
    for chunk in raw.split("\n\n"):
        if not chunk.strip():
            continue
        event_line, data_line = chunk.split("\n", 1)
        assert event_line.startswith("event: "), f"malformed frame: {chunk!r}"
        assert data_line.startswith("data: "), f"malformed frame: {chunk!r}"
        frames.append((event_line.removeprefix("event: "), data_line.removeprefix("data: ")))
    return frames


def test_extract_stream_emits_chunks_then_result(
    settings: Settings, stub_streaming_service: ExtractionService
) -> None:
    """Happy path: the SSE stream emits N `chunk` frames then exactly one `result`."""
    import json as _json

    with _client(settings, stub_streaming_service) as c:
        r = c.post(
            "/extract/stream",
            json={"schema_name": "invoice", "document_text": "doc"},
        )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")

    frames = _parse_sse(r.text)
    chunk_frames = [data for ev, data in frames if ev == "chunk"]
    result_frames = [data for ev, data in frames if ev == "result"]
    error_frames = [data for ev, data in frames if ev == "error"]

    assert len(chunk_frames) > 1, "expected multiple chunk frames"
    assert len(result_frames) == 1, f"expected exactly one result frame; got {len(result_frames)}"
    assert error_frames == []

    # The final result frame's JSON payload is a serialised ExtractionResult.
    result_payload = _json.loads(result_frames[0])
    assert result_payload["schema"] == "invoice"
    assert result_payload["fields"][0]["name"] == "invoice_number"

    # Concatenating the chunk frames must reproduce the JSON the model "emitted".
    concatenated = "".join(_json.loads(c)["text"] for c in chunk_frames)
    assert "INV-001" in concatenated


def test_extract_stream_unknown_schema_returns_404_before_stream(
    settings: Settings, stub_streaming_service: ExtractionService
) -> None:
    """Schema-not-found is rejected with 404 *before* the SSE response opens."""
    with _client(settings, stub_streaming_service) as c:
        r = c.post(
            "/extract/stream",
            json={"schema_name": "no_such", "document_text": "doc"},
        )
    assert r.status_code == 404
    # A 404 is a normal JSON body, NOT an event-stream — verify by content-type.
    assert r.headers["content-type"].startswith("application/json")


def test_extract_stream_mismatched_schema_version_returns_404(
    settings: Settings, stub_streaming_service: ExtractionService
) -> None:
    """Pin mismatch is also a pre-stream 404 (consistent with /extract)."""
    with _client(settings, stub_streaming_service) as c:
        r = c.post(
            "/extract/stream",
            json={
                "schema_name": "invoice",
                "schema_version": "9.9.9-no-such",
                "document_text": "doc",
            },
        )
    assert r.status_code == 404


def test_extract_stream_emits_error_event_on_unparseable_json(
    settings: Settings, stub_streaming_service_returns_garbage: ExtractionService
) -> None:
    """Mid-stream JSON-parse failure surfaces as a terminal `event: error` frame.

    The HTTP status is still 200 — once an SSE response opens the status code
    cannot change, so post-stream errors travel inside the stream itself.
    """
    import json as _json

    with _client(settings, stub_streaming_service_returns_garbage) as c:
        r = c.post(
            "/extract/stream",
            json={"schema_name": "invoice", "document_text": "doc"},
        )
    assert r.status_code == 200
    frames = _parse_sse(r.text)
    assert frames, "expected at least one SSE frame"
    final_event, final_data = frames[-1]
    assert final_event == "error", f"expected terminal 'error' frame; got {final_event!r}"
    detail = _json.loads(final_data)["detail"]
    assert "Could not parse JSON" in detail

    # Sanity: no result frame was emitted before the error.
    assert all(ev != "result" for ev, _ in frames)
