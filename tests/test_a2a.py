"""End-to-end tests for the A2A (agent-to-agent) protocol integration."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bedrock_strands_agent.api.app import create_app
from bedrock_strands_agent.config import Settings
from bedrock_strands_agent.extraction import ExtractionService


def _a2a_settings(base: Settings) -> Settings:
    """Return a copy of `settings` with the A2A endpoint enabled."""
    return base.model_copy(update={"a2a_enabled": True})


def _client(settings: Settings, service: ExtractionService) -> TestClient:
    return TestClient(create_app(settings, extraction_service=service))


def _send_message_body(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 `message/send` envelope."""
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": parts,
                "message_id": str(uuid.uuid4()),
            },
        },
    }


def _extract_data_payload() -> dict[str, str]:
    return {"schema_name": "invoice", "document_text": "INVOICE #INV-1"}


# --------------------------------------------------------------------------- #
# Agent card discovery
# --------------------------------------------------------------------------- #


def test_agent_card_at_canonical_well_known_path(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """The A2A spec mandates the card at `/.well-known/agent-card.json`."""
    with _client(_a2a_settings(settings), stub_extraction_service) as c:
        r = c.get("/.well-known/agent-card.json")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == settings.service_name
    # Exactly one skill: "extract".
    skill_ids = {s["id"] for s in body["skills"]}
    assert skill_ids == {"extract"}
    # Capabilities object is present and reflects what we advertise.
    assert body["capabilities"]["streaming"] is False


def test_agent_card_namespaced_copy_under_a2a(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """The namespaced copy under `/a2a/.well-known/` is the same document.

    Some A2A clients namespace discovery beneath the protocol path; we
    serve the card both places to keep both groups working.
    """
    with _client(_a2a_settings(settings), stub_extraction_service) as c:
        canonical = c.get("/.well-known/agent-card.json").json()
        namespaced = c.get("/a2a/.well-known/agent-card.json").json()
    # Same content, byte-for-byte.
    assert canonical == namespaced


def test_agent_card_url_falls_back_to_local_when_public_url_unset(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """When `A2A_PUBLIC_URL` is unset, the card advertises the local bind addr."""
    with _client(_a2a_settings(settings), stub_extraction_service) as c:
        body = c.get("/.well-known/agent-card.json").json()
    assert body["url"].endswith("/a2a/jsonrpc")


def test_agent_card_uses_explicit_public_url_when_set(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """`A2A_PUBLIC_URL` overrides the local fallback (used in prod)."""
    explicit = settings.model_copy(
        update={
            "a2a_enabled": True,
            "a2a_public_url": "https://extract.example.com/a2a/jsonrpc",
        }
    )
    with _client(explicit, stub_extraction_service) as c:
        body = c.get("/.well-known/agent-card.json").json()
    assert body["url"] == "https://extract.example.com/a2a/jsonrpc"


# --------------------------------------------------------------------------- #
# JSON-RPC message/send
# --------------------------------------------------------------------------- #


def _post_jsonrpc(client: TestClient, parts: list[dict[str, Any]]) -> dict[str, Any]:
    body = _send_message_body(parts)
    r = client.post("/a2a/jsonrpc", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_extract_via_a2a_returns_data_artifact(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """Happy path: a DataPart payload yields an `extraction_result` artifact."""
    with _client(_a2a_settings(settings), stub_extraction_service) as c:
        body = _post_jsonrpc(c, [{"kind": "data", "data": _extract_data_payload()}])
    assert "result" in body
    task = body["result"]
    assert task["status"]["state"] == "completed"
    assert task["artifacts"]
    artifact = task["artifacts"][0]
    assert artifact["name"] == "extraction_result"
    parts = artifact["parts"]
    assert parts[0]["kind"] == "data"
    payload = parts[0]["data"]
    # The full ExtractionResult shape is preserved (camelCase per by_alias).
    assert payload["schema"] == "invoice"
    assert payload["fields"][0]["name"] == "invoice_number"


def test_extract_via_a2a_with_text_part_fails_task(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """A TextPart-only message has no schema_name + document_text; fail loudly.

    The trade-off (rejecting TextPart vs trying to parse it as a JSON
    blob) was decided in favour of rejection: the schema-first contract
    is the whole point of this service, and silently guessing what the
    caller meant from free text would lose the validation pipeline.
    """
    with _client(_a2a_settings(settings), stub_extraction_service) as c:
        body = _post_jsonrpc(
            c,
            [{"kind": "text", "text": "extract this invoice please"}],
        )
    assert "result" in body
    task = body["result"]
    assert task["status"]["state"] == "failed"
    # Failure message must guide the caller to send a DataPart.
    msg = task["status"]["message"]
    failure_text = msg["parts"][0]["text"]
    assert "DataPart" in failure_text


def test_extract_via_a2a_missing_required_field_fails_task(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """A DataPart that's missing `document_text` fails with a clear message."""
    with _client(_a2a_settings(settings), stub_extraction_service) as c:
        body = _post_jsonrpc(
            c,
            [{"kind": "data", "data": {"schema_name": "invoice"}}],
        )
    assert "result" in body
    task = body["result"]
    assert task["status"]["state"] == "failed"
    failure_text = task["status"]["message"]["parts"][0]["text"]
    assert "document_text" in failure_text


def test_extract_via_a2a_unknown_schema_fails_task(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """Unknown `schema_name` becomes a task-failed with a `unknown schema` reason."""
    with _client(_a2a_settings(settings), stub_extraction_service) as c:
        body = _post_jsonrpc(
            c,
            [
                {
                    "kind": "data",
                    "data": {
                        "schema_name": "nope",
                        "document_text": "doc",
                    },
                }
            ],
        )
    assert "result" in body
    task = body["result"]
    assert task["status"]["state"] == "failed"
    text = task["status"]["message"]["parts"][0]["text"]
    assert "unknown schema" in text


# --------------------------------------------------------------------------- #
# Disabled-by-default safety
# --------------------------------------------------------------------------- #


def test_a2a_routes_not_mounted_when_disabled(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """A2A is opt-in; without the setting, none of the endpoints respond."""
    # `settings` fixture has a2a_enabled=False by default.
    with _client(settings, stub_extraction_service) as c:
        card = c.get("/.well-known/agent-card.json")
        rpc = c.post("/a2a/jsonrpc", json={})
    assert card.status_code == 404
    assert rpc.status_code == 404


# --------------------------------------------------------------------------- #
# Auth integration
# --------------------------------------------------------------------------- #


def test_agent_card_is_bypassed_by_auth_middleware(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """Discovery is unauthenticated by spec; AuthMiddleware must skip it."""
    auth_settings = settings.model_copy(
        update={
            "a2a_enabled": True,
            "auth_mode": "apikey",
            "api_keys": ["test-key-must-be-at-least-16-chars"],
        }
    )
    with _client(auth_settings, stub_extraction_service) as c:
        # No X-API-Key header; should still succeed for the card.
        canonical = c.get("/.well-known/agent-card.json")
        namespaced = c.get("/a2a/.well-known/agent-card.json")
    assert canonical.status_code == 200
    assert namespaced.status_code == 200


def test_jsonrpc_endpoint_requires_auth_when_apikey_mode(
    settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """The JSON-RPC endpoint goes through AuthMiddleware (it's the API surface).

    A2A discovery is open by spec; the actual call surface is not.
    """
    auth_settings = settings.model_copy(
        update={
            "a2a_enabled": True,
            "auth_mode": "apikey",
            "api_keys": ["test-key-must-be-at-least-16-chars"],
        }
    )
    body = _send_message_body([{"kind": "data", "data": _extract_data_payload()}])
    with _client(auth_settings, stub_extraction_service) as c:
        # No key: 401.
        unauth = c.post("/a2a/jsonrpc", json=body)
        # With key: 200.
        ok = c.post(
            "/a2a/jsonrpc",
            json=body,
            headers={"X-API-Key": "test-key-must-be-at-least-16-chars"},
        )
    assert unauth.status_code == 401
    assert ok.status_code == 200


# --------------------------------------------------------------------------- #
# Cancellation rejection
# --------------------------------------------------------------------------- #


async def test_cancel_returns_unsupported_directly(
    stub_extraction_service: ExtractionService,
) -> None:
    """Unit-level test: `ExtractionAgentExecutor.cancel` raises ServerError.

    Going through the full JSON-RPC `tasks/cancel` path doesn't exercise
    our executor's cancel() because `DefaultRequestHandler` short-circuits
    on already-terminal tasks (raising `TaskNotCancelableError` BEFORE
    invoking the executor). Test the executor directly instead so the
    "we explicitly decline cancellation" decision has real coverage.
    """
    from unittest.mock import MagicMock

    from a2a.types import UnsupportedOperationError
    from a2a.utils.errors import ServerError

    from bedrock_strands_agent.a2a.executor import ExtractionAgentExecutor

    executor = ExtractionAgentExecutor(stub_extraction_service)
    fake_context = MagicMock()
    fake_context.current_task = None  # exercise the no-task branch
    fake_event_queue = MagicMock()

    with pytest.raises(ServerError) as exc:
        await executor.cancel(fake_context, fake_event_queue)
    assert isinstance(exc.value.error, UnsupportedOperationError)


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/agent-card.json",
        "/a2a/.well-known/agent-card.json",
        "/a2a/jsonrpc",
    ],
)
def test_a2a_routes_404_when_not_enabled(
    path: str, settings: Settings, stub_extraction_service: ExtractionService
) -> None:
    """Parametrised guard: each A2A endpoint is gone when `a2a_enabled=False`.

    Defence-in-depth: a future change that mounts an A2A route
    unconditionally would silently expose the endpoint without operator
    opt-in. This pins the contract.
    """
    with _client(settings, stub_extraction_service) as c:
        r = c.get(path) if "jsonrpc" not in path else c.post(path, json={})
    assert r.status_code == 404
