"""Tenacity-backed retry wrapper for Bedrock model invocations.

Retries transient errors (`ThrottlingException`, `ServiceQuotaExceededException`,
`ModelStreamErrorException`, `InternalServerException`, `ServiceUnavailableException`,
HTTP 5xx) with exponential-jitter backoff. Records a `bedrock.retry` event on the
caller's active span so X-Ray dashboards can plot per-request retry counts and
the SLO `Bedrock retry rate` metric is computable.

Non-retryable errors (e.g. `ValidationException`) propagate on the first call.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError
from opentelemetry import trace
from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from tenacity.wait import wait_base

LOGGER = logging.getLogger(__name__)
_TRACER = trace.get_tracer("bedrock_strands_agent.bedrock_retry")

_RETRYABLE_CODES = frozenset(
    {
        "ThrottlingException",
        "ServiceQuotaExceededException",
        "ModelStreamErrorException",
        "InternalServerException",
        "ServiceUnavailableException",
    }
)
_DEFAULT_ATTEMPTS = 3
_DEFAULT_INITIAL_WAIT = 0.5
_DEFAULT_MAX_WAIT = 10.0


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient Bedrock errors that should be retried."""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        return code in _RETRYABLE_CODES or 500 <= status < 600
    return type(exc).__name__ in _RETRYABLE_CODES


def invoke_with_retry[T](
    callable_: Callable[..., T],
    *args: Any,
    attempts: int = _DEFAULT_ATTEMPTS,
    initial_wait: float = _DEFAULT_INITIAL_WAIT,
    max_wait: float = _DEFAULT_MAX_WAIT,
    wait: wait_base | None = None,
    **kwargs: Any,
) -> T:
    """Call ``callable_`` with retry on transient Bedrock errors.

    Args:
        callable_: The agent or model-invoking callable.
        *args: Positional arguments forwarded to ``callable_``.
        attempts: Maximum total attempts (default ``3`` = 1 initial + 2 retries).
        initial_wait: Base wait for the default exponential-jitter strategy.
        max_wait: Cap on the default exponential-jitter wait.
        wait: Optional explicit tenacity wait strategy. Overrides the
            ``initial_wait``/``max_wait`` defaults — useful for tests
            (``tenacity.wait_none()``).
        **kwargs: Keyword arguments forwarded to ``callable_``.

    Returns:
        Whatever ``callable_`` returned on success. Non-retryable exceptions
        propagate on the first call; retryable exceptions are re-raised after
        ``attempts`` exhausted attempts.
    """
    parent_span = trace.get_current_span()
    wait_strategy: wait_base = (
        wait if wait is not None else wait_exponential_jitter(initial=initial_wait, max=max_wait)
    )

    attempt_no = 0
    for attempt in Retrying(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(attempts),
        wait=wait_strategy,
        reraise=True,
    ):
        with attempt:
            attempt_no += 1
            if attempt_no > 1 and parent_span is not None:
                parent_span.add_event("bedrock.retry", {"attempt": attempt_no})
                LOGGER.warning("Retrying Bedrock call (attempt %d)", attempt_no)
            return callable_(*args, **kwargs)
    # Unreachable: tenacity either returns from the with-block or re-raises
    # after the final attempt. Required to satisfy mypy strict.
    msg = "tenacity Retrying loop exited without return or raise"
    raise RuntimeError(msg)  # pragma: no cover
