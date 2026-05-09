"""Bedrock multimodal vision invocation via the Converse API.

Used by ``ExtractionService._extract_via_vision`` for image-input
extractions. We call the ``bedrock-runtime`` Converse API directly rather
than going through ``Strands.Agent`` because the agent's tool dispatch is
not load-bearing in image-only mode (server-side validators run
post-response regardless). See ADR-0008 for the rationale.

The transient-error retry wrapper from :mod:`bedrock_strands_agent.agent.bedrock_retry`
wraps the call so throttling and 5xx responses are retried with the same
backoff as the text path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

import boto3

from bedrock_strands_agent.agent.bedrock_retry import invoke_with_retry

if TYPE_CHECKING:
    from collections.abc import Iterable

LOGGER = logging.getLogger(__name__)

ImageFormat = Literal["png", "jpeg", "gif", "webp"]


def invoke_multimodal(
    *,
    region: str,
    model_id: str,
    prompt: str,
    images: Iterable[bytes],
    image_format: ImageFormat,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    top_p: float = 0.9,
    client: Any = None,
) -> str:
    """Call Bedrock Converse with the prompt and image content blocks.

    Args:
        region: AWS region (e.g. ``us-east-1``).
        model_id: The Bedrock model identifier; same shape as the env var
            ``BEDROCK_MODEL_ID``.
        prompt: Text prompt placed before the image blocks.
        images: Raw bytes of each image to send. Order is preserved.
        image_format: Format identifier for *all* images in this call.
            Mixed-format batches are not supported by the Converse API.
        max_tokens: Inference cap.
        temperature: Sampling temperature.
        top_p: Top-p sampling.
        client: Optional pre-built ``bedrock-runtime`` client. Tests inject
            a stub; production callers leave this ``None`` and the
            function constructs a fresh client.

    Returns:
        The concatenated text content of the model's response.

    Raises:
        Whatever non-retryable :class:`botocore.exceptions.ClientError` the
        SDK surfaced, or the final retryable error after the configured
        number of attempts.
    """
    bedrock = client if client is not None else boto3.client("bedrock-runtime", region_name=region)

    content_blocks: list[dict[str, Any]] = [{"text": prompt}]
    content_blocks.extend(
        {"image": {"format": image_format, "source": {"bytes": img}}} for img in images
    )

    # boto3-stubs types `messages` as a strict TypedDict union; our nested
    # content blocks are equivalent shape but fail the variance check. Use
    # Any inside the inner callable so the public surface stays typed.
    def _call_converse() -> Any:
        return bedrock.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": content_blocks}],  # type: ignore[list-item, misc]
            inferenceConfig={
                "maxTokens": max_tokens,
                "temperature": temperature,
                "topP": top_p,
            },
        )

    response: dict[str, Any] = invoke_with_retry(_call_converse)
    output_message = response.get("output", {}).get("message", {})
    parts = output_message.get("content", []) or []
    text_parts = [p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p]
    return "".join(text_parts)
