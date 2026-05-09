"""Tests for slowapi-based rate limiting on ``/extract``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from bedrock_strands_agent.api.app import create_app
from bedrock_strands_agent.config import Settings

if TYPE_CHECKING:
    from bedrock_strands_agent.extraction import ExtractionService


def _client(s: Settings, service: ExtractionService) -> TestClient:
    return TestClient(create_app(s, extraction_service=service))


def test_rate_limit_disabled_by_default(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    with _client(settings, stub_extraction_service) as c:
        for _ in range(5):
            r = c.post(
                "/extract",
                json={"schema_name": "invoice", "document_text": "doc"},
            )
            assert r.status_code == 200, r.text


def test_rate_limit_enforces_per_api_key(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    s = settings.model_copy(update={"rate_limit_enabled": True, "rate_limit_per_minute": 2})
    with _client(s, stub_extraction_service) as c:
        for _ in range(2):
            r = c.post(
                "/extract",
                json={"schema_name": "invoice", "document_text": "doc"},
                headers={"X-API-Key": "k1"},
            )
            assert r.status_code == 200, r.text
        # Third call from same key should hit the limit.
        r = c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": "doc"},
            headers={"X-API-Key": "k1"},
        )
        assert r.status_code == 429, r.text


def test_rate_limit_isolates_per_api_key(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    s = settings.model_copy(update={"rate_limit_enabled": True, "rate_limit_per_minute": 1})
    with _client(s, stub_extraction_service) as c:
        # k1 burns its quota.
        c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": "doc"},
            headers={"X-API-Key": "k1"},
        )
        # k2 still has its own quota.
        r = c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": "doc"},
            headers={"X-API-Key": "k2"},
        )
        assert r.status_code == 200, r.text


def test_health_not_rate_limited(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    s = settings.model_copy(update={"rate_limit_enabled": True, "rate_limit_per_minute": 1})
    with _client(s, stub_extraction_service) as c:
        for _ in range(5):
            r = c.get("/health")
            assert r.status_code == 200


def test_schemas_not_rate_limited(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    s = settings.model_copy(update={"rate_limit_enabled": True, "rate_limit_per_minute": 1})
    with _client(s, stub_extraction_service) as c:
        for _ in range(5):
            r = c.get("/schemas")
            assert r.status_code == 200
