"""
Auth0 OTP client — Phase 4.1.

Wraps Auth0's "passwordless" `/passwordless/start` + `/oauth/token`
endpoints. In dev/test we use an in-process `_StubAuth0Client` that
doesn't talk to Auth0 — production injects the real HTTP client.

Why not call Auth0 directly from the route handler?
  1. Testability — handler test cases get a deterministic stub.
  2. Multi-provider future — when we add a second OTP source (e.g.,
     Twilio Verify for a fallback) the route doesn't change shape.

The contract is intentionally minimal:

    client = Auth0OtpClient(domain="dev.auth0.com",
                            client_id=..., client_secret=...,
                            connection="sms")
    await client.start(phone_e164="+525540001234")
    # ...user receives the SMS...
    tokens = await client.verify(phone_e164="+525540001234", otp="123456")
    # → {"access_token": str, "id_token": str, "expires_in": int,
    #    "subject": str}
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol


# ---------------------------------------------------------------------------
# Tokens (subset of Auth0's response we actually use)
# ---------------------------------------------------------------------------
@dataclass
class Auth0Tokens:
    access_token: str
    id_token: str
    expires_in: int
    subject: str            # Auth0 `sub` — opaque, like "sms|abc123…"


class Auth0OtpClient(Protocol):
    async def start(self, *, phone_e164: str, locale: str = "es-MX") -> None: ...
    async def verify(self, *, phone_e164: str, otp: str) -> Auth0Tokens: ...


# ---------------------------------------------------------------------------
# Real implementation (HTTP)
# ---------------------------------------------------------------------------
class _RealAuth0OtpClient:
    """Talks to Auth0's passwordless endpoints. Used in prod / staging."""

    def __init__(
        self,
        *,
        domain: str,
        client_id: str,
        client_secret: str,
        audience: Optional[str] = None,
        connection: str = "sms",
        http_factory: Optional[Callable[[], Any]] = None,
    ):
        if not domain or not client_id or not client_secret:
            raise RuntimeError(
                "Auth0 client requires AUTH0_DOMAIN + AUTH0_CLIENT_ID + "
                "AUTH0_CLIENT_SECRET. Got empties."
            )
        self.domain = domain
        self.client_id = client_id
        self.client_secret = client_secret
        self.audience = audience or f"https://{domain}/api/v2/"
        self.connection = connection
        self._http_factory = http_factory or _default_http_factory

    async def start(self, *, phone_e164: str, locale: str = "es-MX") -> None:
        body = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "connection": self.connection,
            "phone_number": phone_e164,
            "send": "code",
            "authParams": {"audience": self.audience, "scope": "openid profile"},
            "language": locale.split("-")[0],
        }
        async with self._http_factory() as http:
            resp = await http.post(
                f"https://{self.domain}/passwordless/start",
                json=body,
                timeout=10.0,
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Auth0 /passwordless/start failed: HTTP {resp.status_code}"
                )

    async def verify(self, *, phone_e164: str, otp: str) -> Auth0Tokens:
        body = {
            "grant_type": "http://auth0.com/oauth/grant-type/passwordless/otp",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "username": phone_e164,
            "otp": otp,
            "realm": self.connection,
            "audience": self.audience,
            "scope": "openid profile",
        }
        async with self._http_factory() as http:
            resp = await http.post(
                f"https://{self.domain}/oauth/token",
                json=body,
                timeout=10.0,
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Auth0 /oauth/token failed: HTTP {resp.status_code}"
                )
            data = resp.json()
        # The `sub` is inside the id_token; we parse it once.
        sub = _extract_sub(data.get("id_token", ""))
        return Auth0Tokens(
            access_token=data.get("access_token", ""),
            id_token=data.get("id_token", ""),
            expires_in=int(data.get("expires_in", 0)),
            subject=sub,
        )


# ---------------------------------------------------------------------------
# Stub used by dev + tests
# ---------------------------------------------------------------------------
class StubAuth0OtpClient:
    """
    Test/dev stub. `start()` records the phone; `verify()` accepts any
    code that matches the most recently started one (default '000000').
    """

    def __init__(self, *, fixed_code: str = "000000"):
        self.started: list[dict] = []
        self.fixed_code = fixed_code

    async def start(self, *, phone_e164: str, locale: str = "es-MX") -> None:
        self.started.append({"phone_e164": phone_e164, "locale": locale})

    async def verify(self, *, phone_e164: str, otp: str) -> Auth0Tokens:
        if otp != self.fixed_code:
            raise RuntimeError("stub: wrong otp")
        return Auth0Tokens(
            access_token=f"stub-access-{phone_e164}",
            id_token=f"stub-id-{phone_e164}",
            expires_in=3600,
            subject=f"sms|stub-{phone_e164}",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _default_http_factory() -> Any:
    """Lazy import of httpx so callers without the dep can use the stub."""
    import httpx  # type: ignore

    class _Ctx:
        async def __aenter__(self):
            self.client = httpx.AsyncClient()
            return self.client

        async def __aexit__(self, *a):
            await self.client.aclose()

    return _Ctx()


def _extract_sub(id_token: str) -> str:
    """Best-effort subject extraction from a JWT id_token.

    We don't verify the signature here — the JWKS path is in
    `services/_shared/auth_middleware.py`. The subject is informational
    for the identity service's own bookkeeping.
    """
    import base64
    import json as _json
    try:
        parts = id_token.split(".")
        if len(parts) < 2:
            return ""
        b = parts[1]
        pad = "=" * (-len(b) % 4)
        return _json.loads(base64.urlsafe_b64decode(b + pad)).get("sub", "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_default_auth0_client() -> Auth0OtpClient:
    """Pick real vs stub based on env."""
    mode = os.getenv("IDENTITY_AUTH0_MODE", "stub").lower()
    if mode == "real":
        return _RealAuth0OtpClient(
            domain=os.getenv("AUTH0_DOMAIN", ""),
            client_id=os.getenv("AUTH0_CLIENT_ID", ""),
            client_secret=os.getenv("AUTH0_CLIENT_SECRET", ""),
            audience=os.getenv("AUTH0_AUDIENCE"),
            connection=os.getenv("AUTH0_CONNECTION", "sms"),
        )
    return StubAuth0OtpClient(fixed_code=os.getenv("AUTH0_STUB_CODE", "000000"))
