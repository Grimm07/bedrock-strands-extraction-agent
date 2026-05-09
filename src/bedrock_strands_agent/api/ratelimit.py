"""Rate-limiting via slowapi.

Per-API-key (preferred) or per-IP fallback. Decorates ``/extract`` only;
``/health``, ``/metrics``, ``/schemas`` remain unbounded.

The limiter is constructed from ``Settings`` and attached to ``app.state.limiter``
in ``api.app.create_app``. ``slowapi.middleware.SlowAPIMiddleware`` ensures the
limiter sees every request; routes without an explicit ``@limiter.limit(...)``
decorator are unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from slowapi import Limiter
from slowapi.util import get_remote_address

if TYPE_CHECKING:
    from starlette.requests import Request

    from bedrock_strands_agent.config import Settings


def _key_func(request: Request) -> str:
    """Prefer API key for fairness across NAT'd clients; fall back to IP."""
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"key:{api_key}"
    return f"ip:{get_remote_address(request)}"


def build_limiter(settings: Settings) -> Limiter:
    """Build a ``Limiter`` configured from ``settings``.

    The limiter is always constructed; the ``enabled`` flag controls whether
    it actually enforces. ``default_limits`` is intentionally empty: limits
    are opt-in per route via ``@limiter.limit(...)`` so ``/health``,
    ``/metrics`` and ``/schemas`` stay unbounded.
    """
    return Limiter(
        key_func=_key_func,
        default_limits=[],
        enabled=settings.rate_limit_enabled,
    )


def rate_limit_string(settings: Settings) -> str:
    """Return the per-route rate-limit string for ``/extract``."""
    return f"{settings.rate_limit_per_minute}/minute"
