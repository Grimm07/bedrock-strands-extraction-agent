from __future__ import annotations

from fastapi.testclient import TestClient

from bedrock_strands_agent.api.app import create_app
from bedrock_strands_agent.config import Settings
from bedrock_strands_agent.extraction import ExtractionService


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


def test_extract_empty_text(settings: Settings, stub_extraction_service: ExtractionService) -> None:
    with _client(settings, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": ""},
        )
    assert r.status_code == 422


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
