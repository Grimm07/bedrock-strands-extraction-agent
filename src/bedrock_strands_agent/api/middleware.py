"""Correlation-id middleware: read or mint `X-Request-ID` and propagate it."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware

from bedrock_strands_agent.logging import set_correlation_id

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

HEADER = "X-Request-ID"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach a per-request correlation id to logs and responses."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Set the contextvar, dispatch, echo the header, then clear."""
        correlation_id = request.headers.get(HEADER) or uuid4().hex
        request.state.correlation_id = correlation_id
        token_value = correlation_id
        set_correlation_id(token_value)
        try:
            response = await call_next(request)
        finally:
            set_correlation_id(None)
        response.headers[HEADER] = correlation_id
        return response
