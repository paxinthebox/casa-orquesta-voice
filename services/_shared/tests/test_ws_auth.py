"""
P4.8 — WebSocket auth helper + voice-gateway wiring guard.

Hermetic tests:
  1. _extract_ws_token — query string + subprotocol parsing
  2. verify_ws_token — accept happy path
  3. verify_ws_token — reject: missing token, expired, bad signature,
                       tenant mismatch, user mismatch
  4. Structural guard — voice-gateway main.py imports verify_ws_token
                        and calls _authenticate_ws BEFORE ws.accept()

No pytest. No FastAPI required.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent
SERVICES = SHARED.parent
ROOT = SERVICES.parent

# Pin the HS256 secret so the signed token below is reproducible.
os.environ["IDENTITY_JWT_SECRET"] = "ws-auth-test-secret-32+chars-please"

sys.path.insert(0, str(SHARED))

import internal_jwt                                            # noqa: E402
from auth_middleware import (                                  # noqa: E402
    verify_ws_token, _extract_ws_token,
    WS_CLOSE_AUTH_FAILED,
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
# 1. _extract_ws_token — query string + subprotocol
# ====================================================================
section("1. _extract_ws_token — extracts from QS / subprotocol")

expect("empty inputs → empty",                _extract_ws_token() == "")
expect("query token=abc",
       _extract_ws_token(query_string="token=abc") == "abc")
expect("query with other params",
       _extract_ws_token(query_string="x=1&token=xyz&y=2") == "xyz")
expect("URL-encoded plus sign survives",
       _extract_ws_token(query_string="token=a%2Bb") == "a+b")
expect("subprotocol bearer.TOK",
       _extract_ws_token(subprotocol="bearer.TOK") == "TOK")
expect("subprotocol bearer-TOK (dash variant)",
       _extract_ws_token(subprotocol="bearer-TOK") == "TOK")
expect("query wins over subprotocol",
       _extract_ws_token(query_string="token=Q",
                         subprotocol="bearer.S") == "Q")
expect("ignores non-bearer subprotocol",
       _extract_ws_token(subprotocol="json.v1") == "")
expect("constant WS_CLOSE_AUTH_FAILED is 4401",  WS_CLOSE_AUTH_FAILED == 4401)


# ====================================================================
# 2. verify_ws_token — happy path
# ====================================================================
section("2. verify_ws_token — accepts a valid signed token")

# We have to reload internal_jwt and auth_middleware so the secret env
# we just set takes effect (same pattern as the section-5 reload in
# test_identity.py — auth_middleware caches verify_internal_jwt at
# import time and won't re-read INTERNAL_HS_SECRET otherwise).
import importlib                                                  # noqa: E402
importlib.reload(internal_jwt)
import auth_middleware as _amw                                     # noqa: E402
importlib.reload(_amw)
verify_ws_token = _amw.verify_ws_token                              # noqa: F811 — intentional re-bind after reload


good = internal_jwt.issue_internal_jwt(
    tenant_id="tnt_x", user_id="u_y",
    session_id="sess_z", role="buyer",
    ttl_seconds=60,
)

async def _ok():
    return await verify_ws_token(
        query_string=f"token={good}",
        require_tenant_id="tnt_x",
        require_user_id="u_y",
    )

claims = asyncio.run(_ok())
expect("valid token resolves to claims",
       claims.get("tenant_id") == "tnt_x" and claims.get("user_id") == "u_y")


# Subprotocol path also works.
async def _ok_subproto():
    return await verify_ws_token(
        subprotocol=f"bearer.{good}",
        require_tenant_id="tnt_x", require_user_id="u_y",
    )

claims = asyncio.run(_ok_subproto())
expect("valid token via subprotocol works",   claims.get("user_id") == "u_y")


# ====================================================================
# 3. verify_ws_token — rejection paths
# ====================================================================
section("3. verify_ws_token — rejects malformed / mismatched tokens")


def _assert_raises(label: str, coro_fn, expected_substr: str):
    try:
        asyncio.run(coro_fn())
        expect(label, False, "did not raise")
    except ValueError as e:
        expect(label, expected_substr in str(e),
               f"raised but with: {e}")
    except Exception as e:
        expect(label, False, f"wrong exception: {type(e).__name__}: {e}")


_assert_raises("missing token → 'missing token'",
               lambda: verify_ws_token(),
               "missing token")

_assert_raises("garbage token → malformed",
               lambda: verify_ws_token(query_string="token=not.a.jwt",
                                       require_tenant_id="tnt_x",
                                       require_user_id="u_y"),
               "")

# Expired
expired = internal_jwt.issue_internal_jwt(
    tenant_id="tnt_x", user_id="u_y", session_id="sess_z",
    ttl_seconds=-100,
)
_assert_raises("expired token rejected",
               lambda: verify_ws_token(query_string=f"token={expired}",
                                       require_tenant_id="tnt_x",
                                       require_user_id="u_y"),
               "expired")

# Tampered (flip a byte in the payload section)
parts = good.split(".")
tampered = parts[0] + "." + parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B") + "." + parts[2]
_assert_raises("tampered token rejected",
               lambda: verify_ws_token(query_string=f"token={tampered}",
                                       require_tenant_id="tnt_x",
                                       require_user_id="u_y"),
               "bad signature")

# Tenant mismatch
_assert_raises("tenant mismatch rejected",
               lambda: verify_ws_token(query_string=f"token={good}",
                                       require_tenant_id="tnt_other",
                                       require_user_id="u_y"),
               "tenant mismatch")

# User mismatch
_assert_raises("user mismatch rejected",
               lambda: verify_ws_token(query_string=f"token={good}",
                                       require_tenant_id="tnt_x",
                                       require_user_id="u_other"),
               "user mismatch")


# ====================================================================
# 4. Structural wiring guard — voice-gateway main.py
# ====================================================================
section("4. voice-gateway wires WS auth BEFORE ws.accept()")

vg_main = (SERVICES / "voice-gateway" / "main.py").read_text(encoding="utf-8")

expect("voice-gateway imports verify_ws_token",
       "verify_ws_token" in vg_main, "")
expect("voice-gateway imports WS_CLOSE_AUTH_FAILED",
       "WS_CLOSE_AUTH_FAILED" in vg_main, "")
expect("voice-gateway defines _authenticate_ws helper",
       "_authenticate_ws" in vg_main, "")
expect("voice-gateway gates on CO_VOICE_REQUIRE_AUTH env",
       "CO_VOICE_REQUIRE_AUTH" in vg_main, "")
expect("voice-gateway uses WS close code 4401",
       "4401" in vg_main, "")

# Critical structural guard: `_authenticate_ws` must appear BEFORE
# `ws.accept()` inside the _run_session function body. If a future
# refactor moves accept() before the auth call, the WS would be
# accepted then closed — a security regression.
m_def = re.search(r"async def _run_session\b", vg_main)
expect("_run_session function present",        m_def is not None)
if m_def:
    body = vg_main[m_def.start():]
    auth_idx = body.find("_authenticate_ws")
    accept_idx = body.find("ws.accept(")
    expect("_authenticate_ws appears in _run_session body",
           auth_idx > 0, "")
    expect("ws.accept() appears in _run_session body",
           accept_idx > 0, "")
    expect("_authenticate_ws is called BEFORE ws.accept()",
           0 < auth_idx < accept_idx,
           f"auth_idx={auth_idx} accept_idx={accept_idx}")


# Mobile already sends ?token=… per P3.2 — confirm the URL builder.
mobile_vc = (ROOT / "apps" / "mobile" / "src" / "voice"
             / "VoiceClient.ts").read_text(encoding="utf-8")
expect("VoiceClient.ts builds URL with ?token=",
       "?token=${auth}" in mobile_vc or "?token=" in mobile_vc, "")


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
print("  All WS auth assertions green. ✅")
sys.exit(0)
