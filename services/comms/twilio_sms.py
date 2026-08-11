"""
Twilio SMS adapter — Phase 4.3.

Wraps Twilio's `/Messages.json` REST API. Used as the fallback channel
when WhatsApp can't deliver (no 24h window + no template available, or
the user simply doesn't use WhatsApp).

Surface mirrors `WhatsappCloudClient` so the router can stay agnostic:

    result = await client.send(to="+525540001234", body="…", sender_id=None)
    # result.success, result.message_id, result.error_*

Twilio responses:
  201      → success, body has `sid` (we map to `message_id`)
  400/4xx  → permanent error (bad number, opted-out, blocked country)
  429      → rate-limited
  500/5xx  → transient — caller retries with backoff

Notes on internationalisation:
  - Mexican LADA numbers must be E.164 (`+52…`).
  - Twilio counts characters per GSM-7/UCS-2. The default GSM segment
    limit is 160; accented characters force UCS-2 (70 chars). We warn
    via `result.segments` so the router can fall back to a shorter
    rendering rather than billing for two segments.
"""
from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


DEFAULT_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
DEFAULT_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
DEFAULT_SENDER = os.getenv("TWILIO_SENDER", "")  # E.164 / messaging-service SID


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class SmsSendResult:
    success: bool
    provider: str = "twilio_sms"
    message_id: Optional[str] = None
    segments: int = 1
    error_code: Optional[int] = None        # Twilio code (e.g. 21211, 30003)
    http_status: int = 0                    # HTTP status of the underlying call
    error_title: Optional[str] = None
    error_message: Optional[str] = None
    raw: Optional[dict] = field(default_factory=dict)

    @property
    def rate_limited(self) -> bool:
        return self.http_status == 429 or self.error_code == 20429

    @property
    def permanently_failed(self) -> bool:
        # Twilio packs 4xx HTTP for permanent client errors; we look at
        # the HTTP status (not the Twilio inner code, which is a 5-digit
        # taxonomy that doesn't share ranges with HTTP).
        return (400 <= self.http_status < 500) and not self.rate_limited


# ---------------------------------------------------------------------------
# Injectable HTTP
# ---------------------------------------------------------------------------
class _HTTPResponse(Protocol):
    status_code: int
    def json(self) -> Any: ...


class _HTTPClient(Protocol):
    async def post(self, url: str, *, headers: dict, data: dict,
                   timeout: float) -> _HTTPResponse: ...
    async def aclose(self) -> None: ...


HTTPFactory = Callable[[], _HTTPClient]


def _default_http_factory() -> _HTTPClient:
    import httpx  # type: ignore

    class _W:
        def __init__(self): self._c = httpx.AsyncClient()
        async def post(self, url, *, headers, data, timeout):
            return await self._c.post(url, headers=headers, data=data,
                                      timeout=timeout)
        async def aclose(self): await self._c.aclose()

    return _W()


# ---------------------------------------------------------------------------
# Encoding helper
# ---------------------------------------------------------------------------
# GSM 03.38 base alphabet (subset; treat anything else as UCS-2).
_GSM_RE = re.compile(
    r"^[A-Za-z0-9 \n\r@£$¥èéùìòÇØøÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ!\"#¤%&'()*+,\-./:;<=>?¡§¿]*$"
)


def estimate_segments(body: str) -> int:
    """Best-effort GSM/UCS-2 segment counter (Twilio billing matches)."""
    if _GSM_RE.match(body or ""):
        # GSM-7: 160 chars per segment, 153 in concatenated mode.
        n = len(body)
        return 1 if n <= 160 else (n + 152) // 153
    n = len(body)
    return 1 if n <= 70 else (n + 66) // 67


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------
class TwilioSmsClient:
    name = "twilio_sms"

    def __init__(
        self,
        *,
        account_sid: str = DEFAULT_ACCOUNT_SID,
        auth_token: str = DEFAULT_AUTH_TOKEN,
        sender: str = DEFAULT_SENDER,
        http_factory: HTTPFactory = _default_http_factory,
        timeout: float = 10.0,
    ):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.sender = sender
        self._http_factory = http_factory
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.sender)

    def _url(self) -> str:
        return (f"https://api.twilio.com/2010-04-01/Accounts/"
                f"{self.account_sid}/Messages.json")

    def _headers(self) -> dict:
        token = base64.b64encode(
            f"{self.account_sid}:{self.auth_token}".encode("utf-8")
        ).decode("ascii")
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

    async def send(self, *, to: str, body: str,
                   sender_override: Optional[str] = None) -> SmsSendResult:
        if not self.configured and not sender_override:
            return SmsSendResult(
                success=False,
                error_code=0,
                error_title="not_configured",
                error_message="TWILIO_* env vars missing",
                segments=estimate_segments(body),
            )
        data = {
            "To": to,
            "Body": body,
            "From": sender_override or self.sender,
        }
        http = self._http_factory()
        try:
            resp = await http.post(
                self._url(), headers=self._headers(),
                data=data, timeout=self._timeout,
            )
        except Exception as e:
            try: await http.aclose()
            except Exception: pass
            return SmsSendResult(
                success=False,
                error_code=0,
                error_title="transport_error",
                error_message=f"{type(e).__name__}: {e}",
                segments=estimate_segments(body),
            )

        try:
            payload = resp.json() or {}
        except Exception:
            payload = {}
        try: await http.aclose()
        except Exception: pass

        status = int(getattr(resp, "status_code", 0))
        if status in (200, 201):
            sid = payload.get("sid") if isinstance(payload, dict) else None
            return SmsSendResult(
                success=True,
                message_id=sid,
                http_status=status,
                segments=int(payload.get("num_segments") or estimate_segments(body)),
                raw=payload if isinstance(payload, dict) else {},
            )

        # Twilio packs failures into `{code, message, status}` JSON.
        code = (payload.get("code") if isinstance(payload, dict) else None) or status
        msg = (payload.get("message") if isinstance(payload, dict) else None) or f"HTTP {status}"
        return SmsSendResult(
            success=False,
            error_code=int(code),
            http_status=status,
            error_title=f"http_{status}",
            error_message=str(msg),
            segments=estimate_segments(body),
            raw=payload if isinstance(payload, dict) else {},
        )
