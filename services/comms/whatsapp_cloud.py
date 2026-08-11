"""
WhatsApp Cloud API adapter — Phase 4.3.

Wraps Meta's `/v21.0/{phone_number_id}/messages` endpoint for sending
both **template messages** (the path that works outside Meta's 24-hour
customer-service window) and **free-form text** (inside the window).

Why two paths?
  * **Template messages** are required when initiating contact with a
    user (or re-engaging after 24 h of silence). They must be registered
    in Meta Business Manager first.
  * **Free-form text** is allowed only within 24 h of the user's last
    inbound message — Meta returns `code=131047` (re-engagement
    required) if you try outside that window.

The adapter:
  - Tracks the per-user "last inbound" timestamp in an injectable
    `WindowTracker`, so the router can pick template vs text without
    paying a round-trip just to find out.
  - Registers a small library of templates the rest of the system
    expects: `visit_confirmation`, `visit_reminder`,
    `offer_notification`, `otp_fallback`.
  - Surfaces structured errors (`{code, title, message}`) so the
    router's fallback logic can react deterministically.
  - Is injectable end-to-end (HTTP, clock, env) so the standalone test
    runner exercises it without hitting Meta.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_GRAPH_VERSION = os.getenv("WHATSAPP_GRAPH_VERSION", "v21.0")
DEFAULT_PHONE_NUMBER_ID = os.getenv("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "")
DEFAULT_ACCESS_TOKEN = os.getenv("WHATSAPP_CLOUD_ACCESS_TOKEN", "")
WINDOW_SECONDS = int(os.getenv("WHATSAPP_WINDOW_SECONDS", "86400"))  # 24h


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class WhatsappSendResult:
    success: bool
    provider: str = "whatsapp_cloud"
    message_id: Optional[str] = None
    used_template: Optional[str] = None
    error_code: Optional[int] = None
    error_title: Optional[str] = None
    error_message: Optional[str] = None
    raw: Optional[dict] = field(default_factory=dict)

    @property
    def re_engagement_required(self) -> bool:
        """Meta error 131047 — message can only be sent within 24h of last
        user message. Caller should retry with a template."""
        return self.error_code == 131047

    @property
    def template_not_approved(self) -> bool:
        """132000 / 132001 — template name unknown or not approved."""
        return self.error_code in (132000, 132001, 132012)

    @property
    def rate_limited(self) -> bool:
        return self.error_code in (130429, 80007)

    @property
    def permanently_failed(self) -> bool:
        """No point retrying via WhatsApp — fall back to SMS."""
        return (self.error_code in (131026, 131051, 470)
                or (self.error_code is not None
                    and 400 <= self.error_code < 500
                    and not (self.re_engagement_required
                             or self.template_not_approved
                             or self.rate_limited)))


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------
@dataclass
class TemplateDefinition:
    name: str
    language: str
    category: str           # "TRANSACTIONAL" | "MARKETING" | "AUTHENTICATION"
    body: str               # human-readable template (uses {{1}}, {{2}}, …)
    variables: list[str]    # ordered slot names (for documentation)


# The templates the rest of the system expects to be registered in Meta
# Business Manager. The body strings here are the *source of truth* —
# the Meta-side configuration must match exactly or the message is
# rejected with code 132012.
KNOWN_TEMPLATES: dict[str, TemplateDefinition] = {
    "visit_confirmation": TemplateDefinition(
        name="visit_confirmation",
        language="es_MX",
        category="TRANSACTIONAL",
        body=(
            "Hola {{1}}, te confirmamos tu visita a {{2}} el {{3}} a las {{4}}. "
            "Responde CONFIRMAR para confirmar o CAMBIAR para reagendar. "
            "— Casa·Orquesta"
        ),
        variables=["nombre", "direccion", "fecha", "hora"],
    ),
    "visit_reminder": TemplateDefinition(
        name="visit_reminder",
        language="es_MX",
        category="TRANSACTIONAL",
        body=(
            "Recordatorio: tu visita a {{1}} es mañana a las {{2}}. "
            "Tu asesor es {{3}}. — Casa·Orquesta"
        ),
        variables=["direccion", "hora", "asesor"],
    ),
    "offer_notification": TemplateDefinition(
        name="offer_notification",
        language="es_MX",
        category="TRANSACTIONAL",
        body=(
            "Tienes una nueva oferta por {{1}} en {{2}}. "
            "Vence el {{3}}. Abre la app para revisar. — Casa·Orquesta"
        ),
        variables=["monto", "direccion", "vencimiento"],
    ),
    "otp_fallback": TemplateDefinition(
        name="otp_fallback",
        language="es_MX",
        category="AUTHENTICATION",
        body=(
            "Tu código Casa·Orquesta es {{1}}. Expira en 5 minutos. "
            "No lo compartas con nadie."
        ),
        variables=["codigo"],
    ),
}


def get_template(name: str) -> Optional[TemplateDefinition]:
    return KNOWN_TEMPLATES.get(name)


def build_template_payload(
    *,
    to: str,
    template: TemplateDefinition,
    variables: list[str],
) -> dict:
    """Construct the WhatsApp Cloud API body for a template message.

    Variables are positional, matching `{{1}}`, `{{2}}` placeholders.
    """
    if len(variables) != len(template.variables):
        raise ValueError(
            f"template '{template.name}' expects "
            f"{len(template.variables)} variables, got {len(variables)}"
        )
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template.name,
            "language": {"code": template.language},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": v} for v in variables
                    ],
                }
            ],
        },
    }


def build_text_payload(*, to: str, body: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }


# ---------------------------------------------------------------------------
# 24h customer window tracker
# ---------------------------------------------------------------------------
class WindowTracker:
    """In-memory `phone → last_inbound_ts` map. Postgres adapter in P4.5."""

    def __init__(
        self,
        *,
        window_seconds: int = WINDOW_SECONDS,
        now_fn: Callable[[], float] = time.time,
    ):
        self._window = window_seconds
        self._now = now_fn
        self._last_inbound: dict[str, float] = {}

    def record_inbound(self, phone: str, *, ts: Optional[float] = None) -> None:
        self._last_inbound[phone] = ts if ts is not None else self._now()

    def is_open(self, phone: str) -> bool:
        ts = self._last_inbound.get(phone)
        if ts is None:
            return False
        return (self._now() - ts) <= self._window

    def remaining_seconds(self, phone: str) -> int:
        ts = self._last_inbound.get(phone)
        if ts is None:
            return 0
        elapsed = self._now() - ts
        return max(0, int(self._window - elapsed))


# ---------------------------------------------------------------------------
# Injectable HTTP surface
# ---------------------------------------------------------------------------
class _HTTPResponse(Protocol):
    status_code: int
    def json(self) -> Any: ...


class _HTTPClient(Protocol):
    async def post(self, url: str, *, headers: dict, json: dict,
                   timeout: float) -> _HTTPResponse: ...
    async def aclose(self) -> None: ...


HTTPFactory = Callable[[], _HTTPClient]


def _default_http_factory() -> _HTTPClient:
    import httpx  # type: ignore

    class _W:
        def __init__(self): self._c = httpx.AsyncClient()
        async def post(self, url, *, headers, json, timeout):
            return await self._c.post(url, headers=headers, json=json,
                                      timeout=timeout)
        async def aclose(self): await self._c.aclose()

    return _W()


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------
class WhatsappCloudClient:
    name = "whatsapp_cloud"

    def __init__(
        self,
        *,
        phone_number_id: str = DEFAULT_PHONE_NUMBER_ID,
        access_token: str = DEFAULT_ACCESS_TOKEN,
        graph_version: str = DEFAULT_GRAPH_VERSION,
        http_factory: HTTPFactory = _default_http_factory,
        timeout: float = 10.0,
        window: Optional[WindowTracker] = None,
    ):
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.graph_version = graph_version
        self._http_factory = http_factory
        self._timeout = timeout
        self.window = window or WindowTracker()

    @property
    def configured(self) -> bool:
        return bool(self.phone_number_id and self.access_token)

    def _url(self) -> str:
        return (f"https://graph.facebook.com/{self.graph_version}/"
                f"{self.phone_number_id}/messages")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def send_template(
        self, *, to: str, template_name: str, variables: list[str],
    ) -> WhatsappSendResult:
        tpl = get_template(template_name)
        if tpl is None:
            return WhatsappSendResult(
                success=False,
                used_template=template_name,
                error_code=132000,
                error_title="unknown_template",
                error_message=f"template {template_name!r} not registered",
            )
        if not self.configured:
            return WhatsappSendResult(
                success=False,
                used_template=template_name,
                error_code=0,
                error_title="not_configured",
                error_message="WHATSAPP_CLOUD_* env vars missing",
            )
        payload = build_template_payload(to=to, template=tpl, variables=variables)
        return await self._post(payload, used_template=template_name)

    async def send_text(self, *, to: str, body: str) -> WhatsappSendResult:
        if not self.configured:
            return WhatsappSendResult(
                success=False,
                error_code=0,
                error_title="not_configured",
                error_message="WHATSAPP_CLOUD_* env vars missing",
            )
        if not self.window.is_open(to):
            # Don't even try — return a deterministic re-engagement error
            # so the router can switch to a template path without an
            # external round-trip.
            return WhatsappSendResult(
                success=False,
                error_code=131047,
                error_title="re_engagement_required",
                error_message=(
                    "outside 24h window; resend using a registered template"
                ),
            )
        payload = build_text_payload(to=to, body=body)
        return await self._post(payload)

    async def _post(
        self, payload: dict, *, used_template: Optional[str] = None,
    ) -> WhatsappSendResult:
        http = self._http_factory()
        try:
            resp = await http.post(
                self._url(),
                headers=self._headers(),
                json=payload,
                timeout=self._timeout,
            )
        except Exception as e:
            try: await http.aclose()
            except Exception: pass
            return WhatsappSendResult(
                success=False,
                used_template=used_template,
                error_code=0,
                error_title="transport_error",
                error_message=f"{type(e).__name__}: {e}",
            )

        try:
            body = resp.json() or {}
        except Exception:
            body = {}
        try: await http.aclose()
        except Exception: pass

        status = int(getattr(resp, "status_code", 0))
        if status == 200:
            msg_id = ""
            try:
                msg_id = (body.get("messages") or [{}])[0].get("id", "")
            except Exception:
                pass
            return WhatsappSendResult(
                success=True,
                used_template=used_template,
                message_id=msg_id or None,
                raw=body,
            )

        # Non-200: parse Meta's error envelope.
        err = (body.get("error") or {}) if isinstance(body, dict) else {}
        return WhatsappSendResult(
            success=False,
            used_template=used_template,
            error_code=int(err.get("code") or status),
            error_title=err.get("type") or f"http_{status}",
            error_message=err.get("message") or f"HTTP {status}",
            raw=body if isinstance(body, dict) else {},
        )
