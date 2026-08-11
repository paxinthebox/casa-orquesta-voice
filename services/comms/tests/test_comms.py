"""
Comms — P4.3 standalone test runner.

Covers:
  1. Template registry (known templates + payload builder + arity check)
  2. WindowTracker — open/expire/remaining
  3. WhatsappCloudClient — 200, 131047, 132000, 429, transport error
                            (with injected HTTP mock)
  4. TwilioSmsClient — 201, 4xx, 429, segment counting
  5. CommsRouter:
       a) inside-window text path
       b) outside-window template path
       c) outside-window text + no template → SMS fallback
       d) WhatsApp 131047 → template retry
       e) WhatsApp permanent fail → SMS fallback
       f) marketing never falls back
       g) tenant budget cap blocks
       h) dedupe cache returns prior result

No pytest. Hermetic.
"""
from __future__ import annotations

import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, SVC)

from whatsapp_cloud import (                              # noqa: E402
    WhatsappCloudClient, WindowTracker,
    build_template_payload, build_text_payload,
    get_template, KNOWN_TEMPLATES,
)
from twilio_sms import (                                  # noqa: E402
    TwilioSmsClient, estimate_segments,
)
from router import (                                      # noqa: E402
    CommsRouter, SendRequest, TenantBudget,
)


PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def expect(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(label)
        print(f"  ✅ {label}")
    else:
        FAILED.append((label, detail))
        print(f"  ❌ {label}  ← {detail}")


def section(t: str) -> None:
    print()
    print("=" * 70)
    print(f"  {t}")
    print("=" * 70)


# ====================================================================
# 1. Template registry
# ====================================================================
section("1. WhatsApp template registry + payload builders")

expect("known templates registered",
       set(KNOWN_TEMPLATES.keys()) == {
           "visit_confirmation", "visit_reminder",
           "offer_notification", "otp_fallback",
       },
       str(set(KNOWN_TEMPLATES.keys())))

tpl = get_template("visit_confirmation")
expect("visit_confirmation expects 4 variables",
       tpl is not None and len(tpl.variables) == 4)

p = build_template_payload(
    to="+525540001234",
    template=tpl,
    variables=["Paco", "Roma Nte. 123", "12 jun", "10:00"],
)
expect("template payload type is template",  p["type"] == "template")
expect("template payload language is es_MX",
       p["template"]["language"]["code"] == "es_MX")
expect("template payload has 4 text parameters",
       len(p["template"]["components"][0]["parameters"]) == 4)

try:
    build_template_payload(to="+1", template=tpl, variables=["only-one"])
    expect("wrong-arity raises ValueError",   False, "did not raise")
except ValueError:
    expect("wrong-arity raises ValueError",   True)

t = build_text_payload(to="+1", body="Hola")
expect("text payload is type=text",          t["type"] == "text"
       and t["text"]["body"] == "Hola")


# ====================================================================
# 2. WindowTracker
# ====================================================================
section("2. WindowTracker — open / expire / remaining")

clock = [1700000000.0]
w = WindowTracker(window_seconds=86400, now_fn=lambda: clock[0])
expect("no record → window closed",          not w.is_open("+5255"))
w.record_inbound("+5255")
expect("after record → window open",         w.is_open("+5255"))
expect("remaining ≈ 86400",                  86399 <= w.remaining_seconds("+5255") <= 86400)
clock[0] += 100_000
expect("after 100k seconds → window closed", not w.is_open("+5255"))
expect("remaining is 0 after expiry",        w.remaining_seconds("+5255") == 0)


# ====================================================================
# 3. WhatsappCloudClient with mocked HTTP
# ====================================================================
section("3. WhatsappCloudClient — 200 / 131047 / 132000 / 429 / transport")


class _MockResp:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body
    def json(self): return self._body


class _MockHttp:
    def __init__(self, response: _MockResp):
        self.response = response
        self.calls: list[dict] = []
        self.closed = False
    async def post(self, url, *, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json,
                           "timeout": timeout})
        return self.response
    async def aclose(self): self.closed = True


async def _wa_send(response: _MockResp, mode: str = "template"):
    fake = _MockHttp(response)
    cli = WhatsappCloudClient(
        phone_number_id="phn_1", access_token="atk_1",
        http_factory=lambda: fake,
        window=WindowTracker(),
    )
    if mode == "template":
        r = await cli.send_template(
            to="+525540001234", template_name="visit_confirmation",
            variables=["Paco", "Roma 123", "12 jun", "10:00"],
        )
    else:
        # Pretend window is open so send_text issues an HTTP call.
        cli.window.record_inbound("+525540001234")
        r = await cli.send_text(to="+525540001234", body="Hola")
    return r, fake


# 200 OK
r, fake = asyncio.run(_wa_send(_MockResp(
    200, {"messages": [{"id": "wamid.HBgL"}]})))
expect("WA 200 → success + message_id",
       r.success and r.message_id == "wamid.HBgL", repr(r))
expect("URL includes graph version + phone id",
       "graph.facebook.com/v21.0/phn_1/messages" in fake.calls[0]["url"])
expect("Authorization Bearer atk_1",
       fake.calls[0]["headers"]["Authorization"] == "Bearer atk_1")

# 131047 re-engagement required
r, _ = asyncio.run(_wa_send(_MockResp(
    400, {"error": {"code": 131047, "type": "OAuthException",
                    "message": "re-engagement required"}}),
    mode="text"))
expect("WA 131047 surfaces re_engagement_required",
       (not r.success) and r.re_engagement_required, repr(r))

# 132000 template not approved
r, _ = asyncio.run(_wa_send(_MockResp(
    400, {"error": {"code": 132012,
                    "message": "template not approved"}})))
expect("WA 132012 surfaces template_not_approved",
       (not r.success) and r.template_not_approved, repr(r))

# 429 rate limited (Meta uses 130429)
r, _ = asyncio.run(_wa_send(_MockResp(
    429, {"error": {"code": 130429, "message": "rate limited"}})))
expect("WA 130429 surfaces rate_limited",
       (not r.success) and r.rate_limited, repr(r))

# Transport error
async def _wa_throw():
    class _Boom:
        async def post(self, *a, **k): raise RuntimeError("net dead")
        async def aclose(self): pass
    cli = WhatsappCloudClient(
        phone_number_id="x", access_token="y",
        http_factory=lambda: _Boom(), window=WindowTracker(),
    )
    return await cli.send_template(
        to="+1", template_name="visit_confirmation",
        variables=["a", "b", "c", "d"],
    )


r = asyncio.run(_wa_throw())
expect("WA transport error caught",
       (not r.success) and r.error_title == "transport_error",
       repr(r))

# send_text outside the window short-circuits without an HTTP call.
async def _wa_outside_window():
    fake = _MockHttp(_MockResp(200, {}))
    cli = WhatsappCloudClient(
        phone_number_id="phn_x", access_token="atk_x",
        http_factory=lambda: fake, window=WindowTracker(),
    )
    r = await cli.send_text(to="+1", body="hola")
    return r, fake


r, fake = asyncio.run(_wa_outside_window())
expect("send_text outside window → 131047 (no HTTP)",
       (not r.success) and r.re_engagement_required
       and len(fake.calls) == 0,
       f"calls={len(fake.calls)}")


# ====================================================================
# 4. TwilioSmsClient
# ====================================================================
section("4. TwilioSmsClient — 201 / 4xx / 429 / segment counting")


class _TwHttp:
    def __init__(self, response: _MockResp):
        self.response = response
        self.calls: list[dict] = []
        self.closed = False
    async def post(self, url, *, headers, data, timeout):
        self.calls.append({"url": url, "headers": headers, "data": dict(data)})
        return self.response
    async def aclose(self): self.closed = True


async def _sms_send(response: _MockResp):
    fake = _TwHttp(response)
    cli = TwilioSmsClient(
        account_sid="AC1", auth_token="tok", sender="+5215500000000",
        http_factory=lambda: fake,
    )
    r = await cli.send(to="+525540001234", body="hola")
    return r, fake


r, fake = asyncio.run(_sms_send(_MockResp(201, {"sid": "SM123",
                                                "num_segments": "1"})))
expect("SMS 201 → success + sid",            r.success and r.message_id == "SM123",
       repr(r))
expect("Twilio URL targets Accounts/AC1",
       "Accounts/AC1/Messages.json" in fake.calls[0]["url"])
expect("Twilio Authorization is Basic",
       fake.calls[0]["headers"]["Authorization"].startswith("Basic "))
expect("Twilio form data has To/Body/From",
       fake.calls[0]["data"]["To"] == "+525540001234"
       and fake.calls[0]["data"]["Body"] == "hola"
       and fake.calls[0]["data"]["From"] == "+5215500000000")

r, _ = asyncio.run(_sms_send(_MockResp(429, {"code": 20429,
                                             "message": "throttled"})))
expect("SMS 429 surfaces rate_limited",      (not r.success) and r.rate_limited,
       repr(r))

r, _ = asyncio.run(_sms_send(_MockResp(400, {"code": 21211,
                                             "message": "invalid 'To'"})))
expect("SMS 4xx surfaces permanently_failed",
       (not r.success) and r.permanently_failed, repr(r))

# Segment counting
expect("ASCII 160 → 1 segment",              estimate_segments("a" * 160) == 1)
expect("ASCII 161 → 2 segments",             estimate_segments("a" * 161) == 2)
expect("Accent forces UCS-2 (≤70 → 1)",      estimate_segments("á" * 70) == 1)
expect("Accent at 71 → 2 segments",          estimate_segments("á" * 71) == 2)


# ====================================================================
# 5. CommsRouter
# ====================================================================
section("5. CommsRouter — selection + fallback + budget + dedupe")


def make_router(*,
                wa_response: _MockResp,
                sms_response: _MockResp,
                budget_cap: int = 1000,
                window_open_for: list[str] | None = None,
                ):
    wa_http = _MockHttp(wa_response)
    sms_http = _TwHttp(sms_response)
    wa = WhatsappCloudClient(
        phone_number_id="phn_1", access_token="atk_1",
        http_factory=lambda: wa_http, window=WindowTracker(),
    )
    for p in (window_open_for or []):
        wa.window.record_inbound(p)
    sms = TwilioSmsClient(
        account_sid="AC1", auth_token="tok", sender="+5215500000000",
        http_factory=lambda: sms_http,
    )
    return CommsRouter(
        whatsapp=wa, sms=sms,
        budget=TenantBudget(default_cap=budget_cap),
    ), wa_http, sms_http


# a) inside-window free-form text path
async def _case_a():
    router, wa_http, sms_http = make_router(
        wa_response=_MockResp(200, {"messages": [{"id": "w1"}]}),
        sms_response=_MockResp(201, {"sid": "sm1"}),
        window_open_for=["+525540001234"],
    )
    return await router.send(SendRequest(
        tenant_id="tnt_a", to="+525540001234",
        purpose="generic", body="Hola",
    )), wa_http, sms_http


r, wa_http, sms_http = asyncio.run(_case_a())
expect("inside-window: WA text used",        r.success and r.channel == "whatsapp_cloud"
       and r.reason == "ok", repr(r))
expect("inside-window: no SMS attempted",    len(sms_http.calls) == 0)
expect("WA payload was type=text",
       wa_http.calls[0]["json"]["type"] == "text")


# b) outside-window template path (purpose maps automatically)
async def _case_b():
    router, wa_http, sms_http = make_router(
        wa_response=_MockResp(200, {"messages": [{"id": "w2"}]}),
        sms_response=_MockResp(201, {"sid": "sm2"}),
    )
    return await router.send(SendRequest(
        tenant_id="tnt_b", to="+525540009999",
        purpose="visit_confirmation",
        variables=["Paco", "Roma 123", "12 jun", "10:00"],
    )), wa_http, sms_http


r, wa_http, sms_http = asyncio.run(_case_b())
expect("outside-window: template used",      r.success and r.channel == "whatsapp_cloud"
       and r.used_template == "visit_confirmation",
       repr(r))
expect("no SMS attempted (b)",               len(sms_http.calls) == 0)


# c) outside-window body-only (no template) → SMS fallback
async def _case_c():
    router, wa_http, sms_http = make_router(
        wa_response=_MockResp(200, {}),       # never hit
        sms_response=_MockResp(201, {"sid": "sm3"}),
    )
    return await router.send(SendRequest(
        tenant_id="tnt_c", to="+525540007777",
        purpose="generic", body="hola",
    )), wa_http, sms_http


r, wa_http, sms_http = asyncio.run(_case_c())
expect("outside-window text → SMS used",     r.success and r.channel == "twilio_sms"
       and r.reason == "no_template_outside_window",
       repr(r))
expect("WA was NOT called in case c",        len(wa_http.calls) == 0)


# d) Template + 131047 → SMS fallback path
# When the router picks the template path (purpose maps to a template)
# AND that template send returns 131047 (e.g., template not approved
# yet but Meta wraps the rejection in 131047), the router falls through
# to SMS with the original body.
async def _case_d():
    wa_http = _MockHttp(_MockResp(
        400, {"error": {"code": 131047, "message": "re-engagement"}}
    ))
    sms_http = _TwHttp(_MockResp(201, {"sid": "sm_d"}))
    wa = WhatsappCloudClient(
        phone_number_id="phn_d", access_token="atk_d",
        http_factory=lambda: wa_http, window=WindowTracker(),
    )
    sms = TwilioSmsClient(
        account_sid="AC1", auth_token="tok", sender="+1",
        http_factory=lambda: sms_http,
    )
    router = CommsRouter(whatsapp=wa, sms=sms,
                         budget=TenantBudget(default_cap=10))
    r = await router.send(SendRequest(
        tenant_id="tnt_d", to="+525540006666",
        purpose="visit_confirmation", body="Hola texto",
        variables=["Paco", "Roma 123", "12 jun", "10:00"],
    ))
    return r, wa_http, sms_http


r, wa_http, sms_http = asyncio.run(_case_d())
expect("d: template 131047 falls back to SMS",
       r.success and r.channel == "twilio_sms"
       and "whatsapp_fallback" in r.reason,
       repr(r))
expect("d: WA template attempted once",       len(wa_http.calls) == 1
       and wa_http.calls[0]["json"]["type"] == "template")
expect("d: SMS body forwarded",               sms_http.calls[0]["data"]["Body"] == "Hola texto")


# e) WhatsApp permanent fail → SMS fallback
async def _case_e():
    router, wa_http, sms_http = make_router(
        wa_response=_MockResp(403, {"error": {"code": 131026,
                                              "message": "user not on WA"}}),
        sms_response=_MockResp(201, {"sid": "sm5"}),
        window_open_for=["+525540005555"],
    )
    return await router.send(SendRequest(
        tenant_id="tnt_e", to="+525540005555",
        purpose="generic", body="Hola",
    )), wa_http, sms_http


r, wa_http, sms_http = asyncio.run(_case_e())
expect("e: WA permanent → SMS fallback",     r.success and r.channel == "twilio_sms"
       and "whatsapp_fallback" in r.reason,
       repr(r))
expect("e: WA was attempted once",           len(wa_http.calls) == 1)
expect("e: SMS was attempted once",          len(sms_http.calls) == 1)


# f) marketing never falls back
async def _case_f():
    router, wa_http, sms_http = make_router(
        wa_response=_MockResp(403, {"error": {"code": 131026,
                                              "message": "boom"}}),
        sms_response=_MockResp(201, {"sid": "sm_f"}),
    )
    return await router.send(SendRequest(
        tenant_id="tnt_f", to="+525540004444",
        purpose="offer",
        variables=["MXN 5,000,000", "Roma 123", "30 jun"],
    )), wa_http, sms_http


r, wa_http, sms_http = asyncio.run(_case_f())
expect("f: marketing failure does NOT fall back to SMS",
       (not r.success) and r.channel == "whatsapp_cloud"
       and r.reason.startswith("marketing_no_fallback"),
       repr(r))
expect("f: SMS was never attempted",         len(sms_http.calls) == 0)


# g) tenant budget cap blocks
async def _case_g():
    router, wa_http, sms_http = make_router(
        wa_response=_MockResp(200, {"messages": [{"id": "w_g"}]}),
        sms_response=_MockResp(201, {"sid": "sm_g"}),
        budget_cap=2,
    )
    out = []
    for _ in range(4):
        out.append(await router.send(SendRequest(
            tenant_id="tnt_g", to="+525540003333",
            purpose="visit_confirmation",
            variables=["a", "b", "c", "d"],
        )))
    return out, wa_http, sms_http


out, wa_http, sms_http = asyncio.run(_case_g())
expect("g: first 2 sends succeed",
       sum(1 for r in out if r.success) == 2,
       str([r.reason for r in out]))
expect("g: 3rd + 4th blocked with rate_limit",
       all(r.channel == "blocked" and r.reason.startswith("rate_limit")
           for r in out[2:]),
       str([r.reason for r in out[2:]]))


# h) dedupe returns prior result
async def _case_h():
    router, wa_http, sms_http = make_router(
        wa_response=_MockResp(200, {"messages": [{"id": "w_h"}]}),
        sms_response=_MockResp(201, {"sid": "sm_h"}),
    )
    a = await router.send(SendRequest(
        tenant_id="tnt_h", to="+525540002222",
        purpose="visit_confirmation",
        variables=["x", "y", "z", "w"],
        client_dedupe_key="dedupe-1",
    ))
    b = await router.send(SendRequest(
        tenant_id="tnt_h", to="+525540002222",
        purpose="visit_confirmation",
        variables=["x", "y", "z", "w"],
        client_dedupe_key="dedupe-1",
    ))
    return a, b, wa_http


a, b, wa_http = asyncio.run(_case_h())
expect("h: dedupe second call returns deduped result",
       b.reason.startswith("deduped:") and b.deduped_from is not None,
       repr(b))
expect("h: WA HTTP only fired once",         len(wa_http.calls) == 1)


# ====================================================================
# Summary
# ====================================================================
print()
print("=" * 70)
print("  SUMMARY")
print("=" * 70)
print(f"  Passed: {len(PASSED)}")
print(f"  Failed: {len(FAILED)}")
if FAILED:
    for label, detail in FAILED:
        print(f"  ❌ {label}: {detail}")
    sys.exit(1)
print("  All comms (WhatsApp + SMS + Router) assertions green. ✅")
sys.exit(0)
