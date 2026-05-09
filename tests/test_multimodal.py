"""Tests for :func:`bedrock_strands_agent.extraction.multimodal.invoke_multimodal`."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from botocore.exceptions import ClientError
from tenacity import wait_none

from bedrock_strands_agent.extraction.multimodal import invoke_multimodal


def _converse_response(text: str) -> dict[str, Any]:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": text}],
            }
        }
    }


def test_builds_message_with_image_block() -> None:
    client = MagicMock()
    client.converse.return_value = _converse_response('{"fields": []}')
    out = invoke_multimodal(
        region="us-east-1",
        model_id="claude-test",
        prompt="hello",
        images=[b"png-bytes"],
        image_format="png",
        client=client,
    )
    assert out == '{"fields": []}'
    args = client.converse.call_args.kwargs
    assert args["modelId"] == "claude-test"
    msg = args["messages"][0]
    assert msg["role"] == "user"
    blocks = msg["content"]
    assert blocks[0] == {"text": "hello"}
    assert blocks[1] == {"image": {"format": "png", "source": {"bytes": b"png-bytes"}}}


def test_inference_config_passes_settings_through() -> None:
    client = MagicMock()
    client.converse.return_value = _converse_response("ok")
    invoke_multimodal(
        region="us-east-1",
        model_id="m",
        prompt="p",
        images=[b"x"],
        image_format="png",
        max_tokens=2048,
        temperature=0.3,
        top_p=0.7,
        client=client,
    )
    cfg = client.converse.call_args.kwargs["inferenceConfig"]
    assert cfg == {"maxTokens": 2048, "temperature": 0.3, "topP": 0.7}


def test_concatenates_multi_part_text_response() -> None:
    client = MagicMock()
    client.converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": "a"}, {"text": "b"}],
            }
        }
    }
    out = invoke_multimodal(
        region="us-east-1",
        model_id="m",
        prompt="p",
        images=[b"x"],
        image_format="png",
        client=client,
    )
    assert out == "ab"


def test_returns_empty_string_when_no_text_block() -> None:
    client = MagicMock()
    client.converse.return_value = {"output": {"message": {"content": []}}}
    out = invoke_multimodal(
        region="us-east-1",
        model_id="m",
        prompt="p",
        images=[b"x"],
        image_format="png",
        client=client,
    )
    assert out == ""


def test_retries_on_throttling_then_succeeds(monkeypatch: object) -> None:
    """Confirm the retry wrapper kicks in on Bedrock 429."""
    client = MagicMock()
    throttle = ClientError(
        error_response={
            "Error": {"Code": "ThrottlingException"},
            "ResponseMetadata": {"HTTPStatusCode": 429},
        },
        operation_name="Converse",
    )
    client.converse.side_effect = [throttle, _converse_response("ok")]

    # Patch the wait function so the test isn't slow.
    import bedrock_strands_agent.agent.bedrock_retry as br_module

    original = br_module.invoke_with_retry

    def _fast(*a: Any, **kw: Any) -> Any:
        kw["wait"] = wait_none()
        return original(*a, **kw)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "bedrock_strands_agent.extraction.multimodal.invoke_with_retry", _fast
    )
    out = invoke_multimodal(
        region="us-east-1",
        model_id="m",
        prompt="p",
        images=[b"x"],
        image_format="png",
        client=client,
    )
    assert out == "ok"
    assert client.converse.call_count == 2


def test_multiple_images_in_single_call() -> None:
    client = MagicMock()
    client.converse.return_value = _converse_response("ok")
    invoke_multimodal(
        region="us-east-1",
        model_id="m",
        prompt="p",
        images=[b"img1", b"img2", b"img3"],
        image_format="jpeg",
        client=client,
    )
    blocks = client.converse.call_args.kwargs["messages"][0]["content"]
    # 1 text block + 3 image blocks
    assert len(blocks) == 4
    assert blocks[1]["image"]["source"]["bytes"] == b"img1"
    assert blocks[2]["image"]["source"]["bytes"] == b"img2"
    assert blocks[3]["image"]["source"]["bytes"] == b"img3"
