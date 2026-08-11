"""
JWT issuer + verifier — Phase 4.1.

We issue HS256 *internal* tokens that carry `tenant_id`, `user_id`,
`role`, and `sid` (session id). Production also accepts RS256 tokens
signed by Auth0 — that path lives in `services/_shared/auth_middleware.py`.

The internal HS256 path is what the mobile app sees after
`POST /auth/verify` succeeds. The Auth0 RS256 path is what server-to-
server identity exchanges use, plus anything that needs to be verified
by a third party.

Why two algorithms?
  * HS256 with a per-tenant secret keeps the hot-path verification cheap
    (HMAC, no JWKS roundtrip) and lets us rotate by replacing one env var.
  * RS256 via Auth0 JWKS is what Auth0 issues natively; we accept it
    for compatibility with the mobile SDK's "silent refresh" flow.

This module has no FastAPI dependency so the test suite can import it
without installing httpx / fastapi.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

DEFAULT_TTL_SECONDS = int(os.getenv("IDENTITY_JWT_TTL_SECONDS", "3600"))
INTERNAL_HS_SECRET = os.getenv("IDENTITY_JWT_SECRET", "dev-only-do-not-ship-me")
INTERNAL_ISSUER = os.getenv("IDENTITY_JWT_ISSUER", "casaorquesta-identity")
INTERNAL_AUDIENCE = os.getenv("IDENTITY_JWT_AUDIENCE", "casaorquesta-services")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def issue_internal_jwt(
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    role: str = "buyer",
    extras: Optional[dict] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    secret: Optional[str] = None,
    now: Optional[float] = None,
) -> str:
    """Create an HS256 token. Secret defaults to env."""
    now_ts = int(now if now is not None else time.time())
    payload = {
        "iss": INTERNAL_ISSUER,
        "aud": INTERNAL_AUDIENCE,
        "sub": user_id,
        "iat": now_ts,
        "exp": now_ts + ttl_seconds,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "role": role,
        "sid": session_id,
    }
    if extras:
        payload.update(extras)
    return _encode_hs256(payload, secret or INTERNAL_HS_SECRET)


def verify_internal_jwt(
    token: str,
    *,
    secret: Optional[str] = None,
    audience: Optional[str] = None,
    issuer: Optional[str] = None,
    now: Optional[float] = None,
    leeway_seconds: int = 30,
) -> dict:
    """Returns the validated claims. Raises ValueError on any failure."""
    claims = _decode_hs256(token, secret or INTERNAL_HS_SECRET)
    now_ts = int(now if now is not None else time.time())
    exp = int(claims.get("exp", 0))
    if exp + leeway_seconds < now_ts:
        raise ValueError("token expired")
    expected_aud = audience or INTERNAL_AUDIENCE
    if claims.get("aud") != expected_aud:
        raise ValueError(f"bad audience: {claims.get('aud')!r}")
    expected_iss = issuer or INTERNAL_ISSUER
    if claims.get("iss") != expected_iss:
        raise ValueError(f"bad issuer: {claims.get('iss')!r}")
    if "tenant_id" not in claims or "user_id" not in claims:
        raise ValueError("token missing tenant_id/user_id")
    return claims


# ---------------------------------------------------------------------------
# HS256 implementation — no external deps so the standalone tests can
# import this without PyJWT installed.
# ---------------------------------------------------------------------------
def _b64url(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _encode_hs256(payload: dict, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b = _b64url(json.dumps(header, separators=(",", ":"),
                                  sort_keys=True).encode("utf-8"))
    payload_b = _b64url(json.dumps(payload, separators=(",", ":"),
                                   sort_keys=True).encode("utf-8"))
    signing_input = header_b + b"." + payload_b
    sig = hmac.new(secret.encode("utf-8"), signing_input,
                   hashlib.sha256).digest()
    return (signing_input + b"." + _b64url(sig)).decode("ascii")


def _decode_hs256(token: str, secret: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed token")
    header_b, payload_b, sig_b = parts
    try:
        header = json.loads(_b64url_decode(header_b))
    except Exception as e:
        raise ValueError(f"bad header: {e!r}") from e
    if header.get("alg") != "HS256":
        raise ValueError(f"unsupported alg: {header.get('alg')!r}")
    signing_input = (header_b + "." + payload_b).encode("ascii")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input,
                            hashlib.sha256).digest()
    actual_sig = _b64url_decode(sig_b)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("bad signature")
    try:
        return json.loads(_b64url_decode(payload_b))
    except Exception as e:
        raise ValueError(f"bad payload: {e!r}") from e
