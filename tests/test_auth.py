"""Tests for :class:`bedrock_strands_agent.api.auth.AuthMiddleware`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import jwt
import pytest
from fastapi.testclient import TestClient

from bedrock_strands_agent.api.app import create_app
from bedrock_strands_agent.config import Settings

if TYPE_CHECKING:
    from bedrock_strands_agent.extraction import ExtractionService


def _client(s: Settings, service: ExtractionService) -> TestClient:
    return TestClient(create_app(s, extraction_service=service))


# --------------------------------------------------------------------------- #
# auth_mode = none (default)
# --------------------------------------------------------------------------- #
def test_auth_none_allows_extract_without_credentials(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    s = settings.model_copy(update={"auth_mode": "none"})
    with _client(s, stub_extraction_service) as c:
        r = c.post("/extract", json={"schema_name": "invoice", "document_text": "doc"})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# auth_mode = apikey
# --------------------------------------------------------------------------- #
def test_apikey_allows_valid_key(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    s = settings.model_copy(update={"auth_mode": "apikey", "api_keys": ["good-key"]})
    with _client(s, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": "doc"},
            headers={"X-API-Key": "good-key"},
        )
    assert r.status_code == 200, r.text


def test_apikey_rejects_missing_header(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    s = settings.model_copy(update={"auth_mode": "apikey", "api_keys": ["good-key"]})
    with _client(s, stub_extraction_service) as c:
        r = c.post("/extract", json={"schema_name": "invoice", "document_text": "doc"})
    assert r.status_code == 401
    body = r.json()
    assert "API key" in body["detail"]


def test_apikey_rejects_unknown_key(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    s = settings.model_copy(update={"auth_mode": "apikey", "api_keys": ["good-key"]})
    with _client(s, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": "doc"},
            headers={"X-API-Key": "bad-key"},
        )
    assert r.status_code == 401


def test_apikey_401_carries_correlation_id(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    s = settings.model_copy(update={"auth_mode": "apikey", "api_keys": ["good-key"]})
    with _client(s, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": "doc"},
            headers={"X-Request-ID": "req-42"},
        )
    assert r.status_code == 401
    assert r.headers["x-request-id"] == "req-42"
    assert r.json()["correlation_id"] == "req-42"


# --------------------------------------------------------------------------- #
# Excluded paths
# --------------------------------------------------------------------------- #
def test_health_excluded_from_apikey_check(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    s = settings.model_copy(update={"auth_mode": "apikey", "api_keys": ["good-key"]})
    with _client(s, stub_extraction_service) as c:
        r = c.get("/health")
    assert r.status_code == 200


def test_metrics_excluded_from_apikey_check(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    s = settings.model_copy(update={"auth_mode": "apikey", "api_keys": ["good-key"]})
    with _client(s, stub_extraction_service) as c:
        r = c.get("/metrics")
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# auth_mode = jwt (with mocked PyJWKClient + jwt.decode)
# --------------------------------------------------------------------------- #
class _StubKey:
    key = b"stub"


class _StubJWKClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        return

    def get_signing_key_from_jwt(self, token: str) -> _StubKey:
        return _StubKey()


def test_jwt_mode_accepts_when_decode_succeeds(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jwt, "PyJWKClient", _StubJWKClient)
    monkeypatch.setattr(jwt, "decode", lambda *_a, **_kw: {"sub": "u"})

    s = settings.model_copy(
        update={
            "auth_mode": "jwt",
            "jwt_jwks_url": "https://example/.well-known/jwks.json",
            "jwt_issuer": "https://example/",
            "jwt_audience": "test",
        }
    )
    with _client(s, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": "doc"},
            headers={"Authorization": "Bearer stub.token.here"},
        )
    assert r.status_code == 200, r.text


def test_jwt_mode_rejects_invalid_token(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jwt, "PyJWKClient", _StubJWKClient)

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise jwt.InvalidTokenError("expired")

    monkeypatch.setattr(jwt, "decode", _raise)

    s = settings.model_copy(
        update={
            "auth_mode": "jwt",
            "jwt_jwks_url": "https://example/.well-known/jwks.json",
        }
    )
    with _client(s, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": "doc"},
            headers={"Authorization": "Bearer bad.token.here"},
        )
    assert r.status_code == 401


def test_jwt_mode_rejects_missing_authorization_header(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jwt, "PyJWKClient", _StubJWKClient)
    s = settings.model_copy(
        update={
            "auth_mode": "jwt",
            "jwt_jwks_url": "https://example/.well-known/jwks.json",
        }
    )
    with _client(s, stub_extraction_service) as c:
        r = c.post("/extract", json={"schema_name": "invoice", "document_text": "doc"})
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# auth_mode = both
# --------------------------------------------------------------------------- #
def test_both_mode_accepts_apikey(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    s = settings.model_copy(update={"auth_mode": "both", "api_keys": ["good-key"]})
    with _client(s, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": "doc"},
            headers={"X-API-Key": "good-key"},
        )
    assert r.status_code == 200, r.text


def test_both_mode_accepts_jwt(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jwt, "PyJWKClient", _StubJWKClient)
    monkeypatch.setattr(jwt, "decode", lambda *_a, **_kw: {"sub": "u"})
    s = settings.model_copy(
        update={
            "auth_mode": "both",
            "api_keys": ["another-key"],
            "jwt_jwks_url": "https://example/.well-known/jwks.json",
        }
    )
    with _client(s, stub_extraction_service) as c:
        r = c.post(
            "/extract",
            json={"schema_name": "invoice", "document_text": "doc"},
            headers={"Authorization": "Bearer stub.token.here"},
        )
    assert r.status_code == 200, r.text


def test_both_mode_rejects_when_neither(
    settings: Settings,
    stub_extraction_service: ExtractionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jwt, "PyJWKClient", _StubJWKClient)
    s = settings.model_copy(
        update={
            "auth_mode": "both",
            "api_keys": ["good-key"],
            "jwt_jwks_url": "https://example/.well-known/jwks.json",
        }
    )
    with _client(s, stub_extraction_service) as c:
        r = c.post("/extract", json={"schema_name": "invoice", "document_text": "doc"})
    assert r.status_code == 401
