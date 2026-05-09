"""Authentication middleware: API key and Cognito JWT.

Modes (``Settings.auth_mode``):

* ``none``   — no auth (default; suited for behind-mesh deployments).
* ``apikey`` — ``X-API-Key`` header validated against ``Settings.api_keys``.
* ``jwt``    — ``Authorization: Bearer …`` validated against a Cognito JWKS.
* ``both``   — accepts either credential.

Paths in ``Settings.auth_excluded_paths`` (``/health``, ``/metrics``, ``/docs``,
``/openapi.json``, ``/redoc`` by default) bypass the check unconditionally so
liveness probes and metrics scraping never need to carry credentials.

When ``RUNTIME_MODE=agentcore`` (Phase B), AgentCore Runtime's
``custom_jwt_authorizer`` validates JWTs *first*; this middleware should be
configured to ``apikey`` (or ``none``) for defense-in-depth.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import jwt
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from bedrock_strands_agent.api.schemas import ErrorResponse

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp

    from bedrock_strands_agent.config import Settings

LOGGER = logging.getLogger(__name__)

# Cache JWKS for 6 h — Cognito rotates RSA keys infrequently and PyJWKClient
# refreshes on demand if a `kid` miss occurs.
_JWKS_CACHE_LIFESPAN_S = 21_600


class AuthError(Exception):
    """Internal: raised by helpers, caught by ``dispatch`` and turned into 401."""


class JWKSCache:
    """Thin wrapper around ``pyjwt.PyJWKClient`` (swappable in tests)."""

    def __init__(self, jwks_url: str) -> None:
        """Initialise a JWKS client targeting ``jwks_url``."""
        self._client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=_JWKS_CACHE_LIFESPAN_S)

    def get_signing_key(self, token: str) -> jwt.PyJWK:
        """Return the signing key for the JWT's ``kid``."""
        return self._client.get_signing_key_from_jwt(token)


class AuthMiddleware(BaseHTTPMiddleware):
    """Per-request auth check; tolerates missing config when ``auth_mode=none``."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        jwks_cache: JWKSCache | None = None,
    ) -> None:
        """Build the middleware against ``settings``.

        Args:
            app: The downstream ASGI app.
            settings: Service settings carrying ``auth_mode`` and friends.
            jwks_cache: Optional pre-built JWKS cache (tests inject a stub).
        """
        super().__init__(app)
        self._settings = settings
        if settings.auth_mode in ("jwt", "both") and settings.jwt_jwks_url and jwks_cache is None:
            self._jwks: JWKSCache | None = JWKSCache(settings.jwt_jwks_url)
        else:
            self._jwks = jwks_cache

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Apply the configured auth mode or pass through."""
        if (
            self._settings.auth_mode == "none"
            or request.url.path in self._settings.auth_excluded_paths
        ):
            return await call_next(request)
        try:
            self._authorize(request)
        except AuthError as exc:
            return self._unauthorized(request, str(exc))
        return await call_next(request)

    # ------------------------------------------------------------------ #
    def _authorize(self, request: Request) -> None:
        """Apply the configured auth mode; raise ``AuthError`` on failure."""
        mode = self._settings.auth_mode
        api_key_ok = self._has_valid_api_key(request) if mode in ("apikey", "both") else False
        jwt_ok = self._has_valid_jwt(request) if mode in ("jwt", "both") else False
        if mode == "apikey" and not api_key_ok:
            raise AuthError("invalid or missing API key")
        if mode == "jwt" and not jwt_ok:
            raise AuthError("invalid or missing JWT")
        if mode == "both" and not (api_key_ok or jwt_ok):
            raise AuthError("invalid or missing credentials")

    def _has_valid_api_key(self, request: Request) -> bool:
        provided = request.headers.get("X-API-Key")
        return bool(provided) and provided in self._settings.api_keys

    def _has_valid_jwt(self, request: Request) -> bool:
        if self._jwks is None:
            return False
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        token = auth[len("Bearer ") :].strip()
        if not token:
            return False
        try:
            signing_key = self._jwks.get_signing_key(token)
            jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.jwt_audience,
                issuer=self._settings.jwt_issuer,
            )
        except jwt.InvalidTokenError as exc:
            LOGGER.info("JWT rejected: %s", exc)
            return False
        return True

    @staticmethod
    def _unauthorized(request: Request, detail: str) -> JSONResponse:
        body = ErrorResponse(
            detail=detail,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
        return JSONResponse(status_code=401, content=body.model_dump())
