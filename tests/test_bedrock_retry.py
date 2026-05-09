"""Unit tests for :mod:`bedrock_strands_agent.agent.bedrock_retry`."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError
from tenacity import wait_none

from bedrock_strands_agent.agent.bedrock_retry import invoke_with_retry


def _client_error(code: str, status: int) -> ClientError:
    return ClientError(
        error_response={
            "Error": {"Code": code, "Message": "stub"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation_name="InvokeModel",
    )


def test_retries_on_throttling_then_succeeds() -> None:
    calls: list[str] = []

    def flaky(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) < 3:
            raise _client_error("ThrottlingException", 429)
        return "ok"

    result = invoke_with_retry(flaky, "hi", wait=wait_none())
    assert result == "ok"
    assert len(calls) == 3


def test_retries_on_5xx_status_then_succeeds() -> None:
    calls: list[str] = []

    def flaky(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) < 2:
            raise _client_error("InternalFailure", 503)
        return "ok"

    result = invoke_with_retry(flaky, "hi", wait=wait_none())
    assert result == "ok"
    assert len(calls) == 2


def test_does_not_retry_on_validation_exception() -> None:
    calls: list[str] = []

    def fn(prompt: str) -> str:
        calls.append(prompt)
        raise _client_error("ValidationException", 400)

    with pytest.raises(ClientError) as exc:
        invoke_with_retry(fn, "hi", wait=wait_none())
    assert exc.value.response["Error"]["Code"] == "ValidationException"
    assert len(calls) == 1


def test_exhausts_retries_then_reraises_last_error() -> None:
    calls: list[str] = []

    def fn(prompt: str) -> str:
        calls.append(prompt)
        raise _client_error("ThrottlingException", 429)

    with pytest.raises(ClientError):
        invoke_with_retry(fn, "hi", attempts=3, wait=wait_none())
    assert len(calls) == 3


def test_passes_args_and_kwargs_through() -> None:
    seen: dict[str, str] = {}

    def fn(prompt: str, *, schema: str) -> str:
        seen["prompt"] = prompt
        seen["schema"] = schema
        return "ok"

    assert invoke_with_retry(fn, "hi", schema="invoice", wait=wait_none()) == "ok"
    assert seen == {"prompt": "hi", "schema": "invoice"}


def test_retries_on_classname_match_for_non_clienterror() -> None:
    """Errors raised by upstream SDKs without a ClientError wrapper are matched
    by class name (mirrors how Strands sometimes surfaces transient errors).
    """
    calls: list[int] = []

    class ThrottlingException(Exception):
        pass

    def fn(prompt: str) -> str:
        calls.append(1)
        if len(calls) < 2:
            raise ThrottlingException
        return "ok"

    assert invoke_with_retry(fn, "hi", wait=wait_none()) == "ok"
    assert len(calls) == 2
