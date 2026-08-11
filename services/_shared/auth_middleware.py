"""
Shared Auth0 / internal JWT middleware — Phase 4.1.

Every FastAPI service (orchestrator, voice-gateway, listings, scheduling,
documents, payments, comms, identity) installs this middleware on the
routes that need authentication. It accepts *both*:

  1. The internal HS256 JWT issued by `services/identity` (this is what
     the mobile app holds after `POST /auth/verify` succeeds), and

  2. Auth0-issued RS256 JWTs verified against the Auth0 JWKS endpoint.
     This path is for server-to-server flows and for anyone holding an
     Auth0 access_token directly (e.g., admin tooling).

Validated claims are attached to `request.state.auth` so downstream
handlers can read `request.state.auth["tenant_id"]` / `"user_id"` /
`"role"` / `"sid"`.

The middleware also injects two FastAPI `Depends()` helpers:

    from services._shared.auth_middleware import (
        AuthInjector, current_user_id, require_role,
    )

    app.add_middleware(AuthInjector)

    @app.get("/private")
    async def private(user_id: str = Depends(current_user_id)):
        ...

The middleware never blocks — it parses the header and attaches claims
if present. Routes that *require* auth use the `Depends(current_user_id)`
helper which raises 401 on missing claims. This split keeps health-check
endpoints reachable without a token.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

# These imports stay lazy so services without FastAPI / httpx installed
# (e.g. the standalone test runner) can still import the verifier.
try:
    from fastapi import HTTPException, Request           # type: ignore
    from starlette.middleware.base import BaseHTTPMiddleware  # type: ignore
    _HAS_FASTAPI = True
except Exception:    # pragma: no cover — sandbox without FastAPI
    _HAS_FASTAPI = False

# Reuse the identity service's HS256 path so internal tokens are
# verified by exactly the same code that issues them. The canonical
# implementation lives in `services/_shared/internal_jwt.py` since P4.6;
# the older names are kept as fallbacks so the import works regardless
# of which service's sys.path is in effect.
verify_internal_jwt = None  # type: ignore[assignment]
for _candidate in ("internal_jwt", "jwt_issuer",
                   "services.identity.jwt_issuer"):
    try:
        _mod = __import__(_candidate, fromlist=["verify_internal_jwt"])
        verify_internal_jwt = getattr(_mod, "verify_internal_jwt", None)
        if verify_internal_jwt is not None:
            break
    except Exception:
        continue


JWKS_URL = os.getenv("AUTH0_JWKS_URL", "")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "")
AUTH0_ISSUER = os.getenv("AUTH0_ISSUER", "")
JWKS_TTL_SECONDS = int(os.getenv("AUTH0_JWKS_TTL_SECONDS", "3600"))


# ---------------------------------------------------------------------------
# JWKS cache — refresh in-process, not per-request. The cache holder is
# injectable so tests pin a deterministic JWKS without HTTP.
# ---------------------------------------------------------------------------
@dataclass
class _CachedJwks:
    keys: dict[str, dict]     # kid → JWK
    fetched_at: float


class JwksCache:
    def __init__(self, *, fetcher: Optional[Callable[[], Any]] = None,
                 ttl_seconds: int = JWKS_TTL_SECONDS,
                 now_fn: Callable[[], float] = time.time):
        self._fetcher = fetcher or _default_jwks_fetcher
        self._ttl = ttl_seconds
        self._now = now_fn
        self._cached: Optional[_CachedJwks] = None

    async def get(self, kid: str) -> dict | None:
        if self._cached and (self._now() - self._cached.fetched_at) < self._ttl:
            return self._cached.keys.get(kid)
        await self._refresh()
        if self._cached:
            return self._cached.keys.get(kid)
        return None

    async def _refresh(self) -> None:
        try:
            jwks = await self._fetcher()
        except Exception:
            # Keep the stale cache rather than blocking auth.
            return
        keys: dict[str, dict] = {}
        for k in jwks.get("keys", []):
            kid = k.get("kid")
            if isinstance(kid, str):
                keys[kid] = k
        self._cached = _CachedJwks(keys=keys, fetched_at=self._now())


async def _default_jwks_fetcher() -> dict:
    """HTTP fetch — only used in prod. Lazy-imports httpx."""
    import httpx                                              # type: ignore
    if not JWKS_URL:
        return {"keys": []}
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(JWKS_URL)
        return r.json() if r.status_code < 400 else {"keys": []}


# Module-level singleton used by the middleware. Tests inject a custom
# JwksCache via `install_jwks_cache(cache)`.
_jwks: JwksCache = JwksCache()


def install_jwks_cache(cache: JwksCache) -> None:
    global _jwks
    _jwks = cache


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------
async def verify_any_token(token: str, *, now: Optional[float] = None) -> dict:
    """
    Validate either an internal HS256 token or an Auth0 RS256 token.

    Returns a dict with the canonical claim names every downstream
    service expects: `tenant_id`, `user_id`, `role`, `sid`.

    Raises ValueError on any failure.
    """
    if not token:
        raise ValueError("empty token")
    header = _decode_jwt_header(token)
    alg = header.get("alg")
    if alg == "HS256":
        if verify_internal_jwt is None:
            raise ValueError("internal HS256 verifier not available")
        return verify_internal_jwt(token, now=now)
    if alg == "RS256":
        return await _verify_rs256(token, header, now=now)
    raise ValueError(f"unsupported alg: {alg!r}")


async def _verify_rs256(token: str, header: dict,
                        *, now: Optional[float] = None) -> dict:
    """Verify an Auth0 RS256 JWT against the JWKS + audience + issuer."""
    kid = header.get("kid")
    if not isinstance(kid, str):
        raise ValueError("RS256 token missing kid")
    jwk = await _jwks.get(kid)
    if jwk is None:
        raise ValueError(f"unknown kid: {kid}")

    # Verifying RS256 properly requires `cryptography`. We import lazily
    # so the standalone test runner can stub this path.
    try:
        import jwt as pyjwt                                  # type: ignore
        from jwt.algorithms import RSAAlgorithm              # type: ignore
    except Exception as e:
        raise ValueError(f"PyJWT not available: {e}") from e

    pubkey = RSAAlgorithm.from_jwk(json.dumps(jwk))
    try:
        claims = pyjwt.decode(
            token,
            pubkey,
            algorithms=["RS256"],
            audience=AUTH0_AUDIENCE or None,
            issuer=AUTH0_ISSUER or None,
            leeway=30,
        )
    except Exception as e:
        raise ValueError(f"RS256 verify failed: {e}") from e

    # Auth0 tokens use namespaced custom claims. The mobile build sets
    # `https://casaorquesta.mx/tenant_id` and `…/role` via a post-login
    # action; we normalize them here.
    return _normalize_auth0_claims(claims)


def _normalize_auth0_claims(claims: dict) -> dict:
    namespace = os.getenv("AUTH0_NAMESPACE", "https://casaorquesta.mx/")
    tenant_id = (claims.get(namespace + "tenant_id")
                 or claims.get("tenant_id"))
    user_id   = (claims.get(namespace + "user_id")
                 or claims.get("user_id")
                 or claims.get("sub"))
    role      = (claims.get(namespace + "role")
                 or claims.get("role")
                 or "buyer")
    sid       = (claims.get("sid")
                 or claims.get("session_id")
                 or "")
    if not tenant_id or not user_id:
        raise ValueError("Auth0 token missing tenant_id / user_id custom claims")
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "role": role,
        "sid": sid,
        "raw": claims,
    }


def _decode_jwt_header(token: str) -> dict:
    import base64
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed token")
    header_b = parts[0]
    pad = "=" * (-len(header_b) % 4)
    return json.loads(base64.urlsafe_b64decode(header_b + pad))


# ---------------------------------------------------------------------------
# Starlette middleware + FastAPI deps
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# WebSocket auth helpers — Phase 4.8
#
# The `AuthInjector` middleware only covers HTTP routes; FastAPI/Starlette
# bypass middlewares entirely for the WS handshake. Services exposing WS
# endpoints (today: voice-gateway) need to validate the token themselves
# *before* calling `ws.accept()` — once accepted, a denial is just a close.
#
# We accept the token from two places, in order:
#   1. `?token=<jwt>` query string  (mobile uses this; matches what
#      `apps/mobile/src/voice/VoiceClient.ts` already sends)
#   2. `Sec-WebSocket-Protocol: bearer.<jwt>` subprotocol header
#      (browser-friendly path — JS WebSocket API can set subprotocols
#      but cannot set arbitrary headers).
#
# Both forms produce the same `dict` claims as the HTTP path; downstream
# code can use them identically.
# ---------------------------------------------------------------------------
def _extract_ws_token(query_string: str = "", subprotocol: str = "") -> str:
    """Pull the raw token from either source. Returns "" if absent."""
    if query_string:
        # Tiny URL-decoder: we don't need anything beyond '%2B' and '%3D'
        # because tokens are base64url-encoded which is URL-safe.
        from urllib.parse import parse_qs
        params = parse_qs(query_string)
        tok = (params.get("token") or [""])[0]
        if tok:
            return tok
    if subprotocol:
        # Expected shape: "bearer.<jwt>" or "bearer-<jwt>" (some clients
        # mangle the dot).
        for sep in (".", "-"):
            if subprotocol.startswith(f"bearer{sep}"):
                return subprotocol[len(f"bearer{sep}"):]
    return ""


async def verify_ws_token(
    *,
    query_string: str = "",
    subprotocol: str = "",
    require_tenant_id: Optional[str] = None,
    require_user_id: Optional[str] = None,
    now: Optional[float] = None,
) -> dict:
    """
    Validate the WS handshake's bearer token. Raises `ValueError` on
    any failure (missing, malformed, expired, wrong audience, wrong
    issuer, tenant/user mismatch).

    The caller is expected to translate that into a WS close with code
    4401 (auth failed) — see voice-gateway's `_run_session` for the
    pattern.
    """
    token = _extract_ws_token(query_string, subprotocol)
    if not token:
        raise ValueError("missing token")
    claims = await verify_any_token(token, now=now)
    if require_tenant_id is not None and claims.get("tenant_id") != require_tenant_id:
        raise ValueError(
            f"tenant mismatch: token has {claims.get('tenant_id')!r}, "
            f"URL requires {require_tenant_id!r}"
        )
    if require_user_id is not None and claims.get("user_id") != require_user_id:
        raise ValueError(
            f"user mismatch: token has {claims.get('user_id')!r}, "
            f"URL requires {require_user_id!r}"
        )
    return claims


# WebSocket close code for auth failures. 4401 is the conventional
# 401-analogue in the application-defined range (4000-4999). 4000 means
# normal close; 4401 specifically signals "authentication required".
WS_CLOSE_AUTH_FAILED = 4401


if _HAS_FASTAPI:
    class AuthInjector(BaseHTTPMiddleware):       # type: ignore[misc, valid-type]
        """
        Reads `Authorization: Bearer <token>` and stashes the validated
        claims under `request.state.auth`. Never raises — routes opt in
        to required auth via `Depends(current_user_id)`.
        """

        async def dispatch(self, request: Request, call_next):
            request.state.auth = None
            auth = request.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
                try:
                    request.state.auth = await verify_any_token(token)
                except ValueError:
                    # Leave state.auth as None — let Depends() raise 401
                    # if the route requires it.
                    pass
            return await call_next(request)

    def current_claims(request: Request) -> dict:
        claims = getattr(request.state, "auth", None)
        if not claims:
            raise HTTPException(status_code=401, detail="missing or invalid token")
        return claims

    def current_user_id(request: Request) -> str:
        return current_claims(request)["user_id"]

    def current_tenant_id(request: Request) -> str:
        return current_claims(request)["tenant_id"]

    def require_role(*allowed: str):
        """Dependency factory — `Depends(require_role('admin'))`."""
        def _dep(request: Request) -> dict:
            claims = current_claims(request)
            if claims["role"] not in allowed:
                raise HTTPException(status_code=403, detail="forbidden")
            return claims
        return _dep
else:
    # Stubs so importing this file outside FastAPI doesn't blow up.
    class AuthInjector:                            # type: ignore[no-redef]
        pass

    def current_claims(*_a, **_k) -> dict:         # type: ignore[no-redef]
        raise RuntimeError("fastapi not installed")

    def current_user_id(*_a, **_k) -> str:         # type: ignore[no-redef]
        raise RuntimeError("fastapi not installed")

    def current_tenant_id(*_a, **_k) -> str:       # type: ignore[no-redef]
        raise RuntimeError("fastapi not installed")

    def require_role(*_allowed):                   # type: ignore[no-redef]
        def _dep(*_a, **_k):
            raise RuntimeError("fastapi not installed")
        return _dep
