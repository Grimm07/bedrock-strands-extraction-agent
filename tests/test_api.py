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
