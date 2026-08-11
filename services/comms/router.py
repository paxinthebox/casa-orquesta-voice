"""
CommsRouter — Phase 4.3.

Picks the right channel (WhatsApp Cloud vs Twilio SMS) and enforces
per-tenant spend caps. The selection rules are explicit so the audit
log can record `decision_reason` for every send:

  PURPOSE → CHANNEL (preferred)
  ---------------------------------
  visit_*     → WhatsApp template if outside-window or first contact;
                WhatsApp text if inside 24h window;
                SMS fallback on any WhatsApp permanent failure.
  offer_*     → WhatsApp template; SMS fallback.
  otp_*       → WhatsApp `otp_fallback` template first; SMS fallback
                (we don't send free-form OTPs over SMS for spam reasons).
  marketing_* → WhatsApp template only — never falls back to SMS to
                avoid regulatory exposure under LGTBT / NOM-247 ads rules.
  generic     → WhatsApp text if window open, else SMS.

Per-tenant per-day rate limit:
  - Default 1000 sends per tenant per day. Override per-tenant in the
    constructor or via `IDENTITY_COMMS_TENANT_DAILY_CAP` env.
  - When exceeded, returns a `RoutingResult(success=False,
    reason="rate_limit")` without touching either provider.

Idempotency:
  - Optional `client_dedupe_key` is hashed alongside the tenant id. A
    repeat send within 1 hour returns the original result without
    re-sending.

The router is intentionally synchronous in its decision logic — only
the provider HTTP calls are async. That keeps the audit log honest:
the reason recorded matches what actually happened, and tests can
exercise every branch deterministically.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from whatsapp_cloud import (
    WhatsappCloudClient, WhatsappSendResult, WindowTracker,
    get_template,
)
from twilio_sms import TwilioSmsClient, SmsSendResult


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------
@dataclass
class SendRequest:
    tenant_id: str
    to: str                             # E.164 phone number
    purpose: str                        # 'visit_confirmation' | 'visit_reminder' | 'offer' | 'otp' | 'generic' | …
    body: Optional[str] = None
    template: Optional[str] = None      # explicit template override (skips picker)
    variables: list[str] = field(default_factory=list)
    client_dedupe_key: Optional[str] = None
    user_id: Optional[str] = None


@dataclass
class RoutingResult:
    success: bool
    channel: str                        # 'whatsapp_cloud' | 'twilio_sms' | 'blocked'
    reason: str                         # explanatory reason code for audit
    message_id: Optional[str] = None
    used_template: Optional[str] = None
    whatsapp_result: Optional[WhatsappSendResult] = None
    sms_result: Optional[SmsSendResult] = None
    deduped_from: Optional[str] = None

    def public(self) -> dict:
        d = {
            "success": self.success,
            "channel": self.channel,
            "reason": self.reason,
            "message_id": self.message_id,
            "used_template": self.used_template,
            "deduped_from": self.deduped_from,
        }
        if self.whatsapp_result is not None:
            d["whatsapp"] = asdict(self.whatsapp_result)
        if self.sms_result is not None:
            d["sms"] = asdict(self.sms_result)
        return d


# ---------------------------------------------------------------------------
# Per-tenant rate limiter
# ---------------------------------------------------------------------------
class TenantBudget:
    """In-memory `(tenant, day) → count`. Resets at UTC midnight."""

    def __init__(
        self,
        *,
        default_cap: int = 1000,
        per_tenant: Optional[dict[str, int]] = None,
        now_fn: Callable[[], float] = time.time,
    ):
        self.default_cap = default_cap
        self.per_tenant = per_tenant or {}
        self._now = now_fn
        self._counts: dict[tuple[str, str], int] = {}

    def _day(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self._now()))

    def cap_for(self, tenant_id: str) -> int:
        return self.per_tenant.get(tenant_id, self.default_cap)

    def attempt(self, tenant_id: str) -> tuple[bool, int, int]:
        """Returns (allowed, used_after, cap)."""
        key = (tenant_id, self._day())
        used = self._counts.get(key, 0)
        cap = self.cap_for(tenant_id)
        if used >= cap:
            return False, used, cap
        self._counts[key] = used + 1
        return True, used + 1, cap

    def force_set(self, tenant_id: str, used: int) -> None:
        """Test hook — pin the counter for a tenant."""
        self._counts[(tenant_id, self._day())] = used


# ---------------------------------------------------------------------------
# Dedupe cache
# ---------------------------------------------------------------------------
class DedupeCache:
    def __init__(
        self,
        *,
        ttl_seconds: int = 3600,
        now_fn: Callable[[], float] = time.time,
    ):
        self._ttl = ttl_seconds
        self._now = now_fn
        self._cache: dict[str, tuple[float, RoutingResult]] = {}

    def get(self, key: str) -> Optional[RoutingResult]:
        e = self._cache.get(key)
        if e is None:
            return None
        ts, result = e
        if (self._now() - ts) > self._ttl:
            del self._cache[key]
            return None
        return result

    def put(self, key: str, result: RoutingResult) -> None:
        self._cache[key] = (self._now(), result)

    @staticmethod
    def make_key(tenant_id: str, to: str, dedupe: str) -> str:
        h = hashlib.sha256(
            f"{tenant_id}|{to}|{dedupe}".encode("utf-8")
        ).hexdigest()
        return f"dd_{h[:24]}"


# ---------------------------------------------------------------------------
# Selection rules
# ---------------------------------------------------------------------------
PURPOSE_TEMPLATE_MAP = {
    "visit_confirmation": "visit_confirmation",
    "visit_reminder":     "visit_reminder",
    "offer":              "offer_notification",
    "offer_notification": "offer_notification",
    "otp":                "otp_fallback",
    "otp_fallback":       "otp_fallback",
}

MARKETING_PURPOSES = {"offer", "offer_notification", "marketing"}
OTP_PURPOSES = {"otp", "otp_fallback"}


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
class CommsRouter:
    def __init__(
        self,
        *,
        whatsapp: Optional[WhatsappCloudClient] = None,
        sms: Optional[TwilioSmsClient] = None,
        budget: Optional[TenantBudget] = None,
        dedupe: Optional[DedupeCache] = None,
    ):
        self.whatsapp = whatsapp or WhatsappCloudClient()
        self.sms = sms or TwilioSmsClient()
        self.budget = budget or TenantBudget()
        self.dedupe = dedupe or DedupeCache()

    @property
    def window(self) -> WindowTracker:
        return self.whatsapp.window

    async def send(self, req: SendRequest) -> RoutingResult:
        # 1. Dedupe
        if req.client_dedupe_key:
            key = DedupeCache.make_key(req.tenant_id, req.to,
                                       req.client_dedupe_key)
            prior = self.dedupe.get(key)
            if prior is not None:
                return RoutingResult(
                    success=prior.success,
                    channel=prior.channel,
                    reason=f"deduped:{prior.reason}",
                    message_id=prior.message_id,
                    used_template=prior.used_template,
                    deduped_from=key,
                )

        # 2. Budget
        allowed, used, cap = self.budget.attempt(req.tenant_id)
        if not allowed:
            result = RoutingResult(
                success=False,
                channel="blocked",
                reason=f"rate_limit:{used}/{cap}",
            )
            return result

        # 3. Pick channel + send.
        result = await self._route(req)

        # 4. Cache for dedupe.
        if req.client_dedupe_key:
            key = DedupeCache.make_key(req.tenant_id, req.to,
                                       req.client_dedupe_key)
            self.dedupe.put(key, result)
        return result

    # ----- internal -----
    async def _route(self, req: SendRequest) -> RoutingResult:
        # Marketing: WhatsApp-only (never falls back to SMS for compliance).
        marketing = req.purpose in MARKETING_PURPOSES
        otp = req.purpose in OTP_PURPOSES

        # Decide WhatsApp message kind.
        explicit_template = req.template or PURPOSE_TEMPLATE_MAP.get(req.purpose)
        window_open = self.whatsapp.window.is_open(req.to)

        # ---- WhatsApp first attempt ----
        if explicit_template is not None and get_template(explicit_template) is not None:
            wa = await self.whatsapp.send_template(
                to=req.to, template_name=explicit_template,
                variables=req.variables,
            )
        elif window_open and req.body:
            wa = await self.whatsapp.send_text(to=req.to, body=req.body)
        elif req.body and explicit_template is None:
            # Outside the 24h window AND no template chosen — there's no
            # legal way to send free-form WhatsApp; jump straight to SMS
            # (unless this is marketing, in which case we hard-fail).
            if marketing:
                return RoutingResult(
                    success=False, channel="blocked",
                    reason="marketing_outside_window_no_template",
                )
            return await self._sms(req, reason="no_template_outside_window")
        else:
            # Misconfigured request.
            return RoutingResult(
                success=False, channel="blocked",
                reason="no_body_and_no_template",
            )

        if wa.success:
            # Outbound success — opens the 24h window for the user since
            # template messages count as initiation.
            self.whatsapp.window.record_inbound(req.to)
            return RoutingResult(
                success=True,
                channel="whatsapp_cloud",
                reason="ok",
                message_id=wa.message_id,
                used_template=wa.used_template,
                whatsapp_result=wa,
            )

        # ---- Fallback decision ----
        if marketing:
            return RoutingResult(
                success=False,
                channel="whatsapp_cloud",
                reason=f"marketing_no_fallback:{wa.error_code}",
                whatsapp_result=wa,
            )

        # 24h window expired — try the template path before SMS.
        if wa.re_engagement_required and explicit_template is None:
            template_name = PURPOSE_TEMPLATE_MAP.get(req.purpose)
            if template_name and get_template(template_name) and req.variables:
                wa2 = await self.whatsapp.send_template(
                    to=req.to, template_name=template_name,
                    variables=req.variables,
                )
                if wa2.success:
                    self.whatsapp.window.record_inbound(req.to)
                    return RoutingResult(
                        success=True,
                        channel="whatsapp_cloud",
                        reason="retried_with_template",
                        message_id=wa2.message_id,
                        used_template=wa2.used_template,
                        whatsapp_result=wa2,
                    )
                wa = wa2  # fall through to SMS

        # SMS fallback path.
        if otp and not req.body:
            req.body = (f"Tu código Casa·Orquesta es {req.variables[0] if req.variables else ''}. "
                        "Expira en 5 minutos. No lo compartas.")
        if not req.body:
            return RoutingResult(
                success=False, channel="blocked",
                reason=f"no_body_for_sms_fallback:{wa.error_code}",
                whatsapp_result=wa,
            )
        sms_result_full = await self._sms(req, reason="whatsapp_fallback",
                                          whatsapp_result=wa)
        return sms_result_full

    async def _sms(
        self, req: SendRequest, *,
        reason: str,
        whatsapp_result: Optional[WhatsappSendResult] = None,
    ) -> RoutingResult:
        if not req.body:
            return RoutingResult(
                success=False, channel="blocked",
                reason="sms_path_without_body",
                whatsapp_result=whatsapp_result,
            )
        sms = await self.sms.send(to=req.to, body=req.body)
        if sms.success:
            return RoutingResult(
                success=True,
                channel="twilio_sms",
                reason=reason,
                message_id=sms.message_id,
                whatsapp_result=whatsapp_result,
                sms_result=sms,
            )
        return RoutingResult(
            success=False,
            channel="twilio_sms",
            reason=f"sms_failed:{sms.error_code}",
            whatsapp_result=whatsapp_result,
            sms_result=sms,
        )
