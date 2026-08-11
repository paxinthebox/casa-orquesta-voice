"""
Identity service — P4.1 standalone test runner.

Covers:
  1. IdentityStore happy paths (tenants, users, consent, sessions, OTP)
  2. JWT issuer/verifier (HS256 round-trip, bad signature, expired,
     bad audience, bad issuer, missing claims)
  3. StubAuth0OtpClient behavior
  4. /auth/start + /auth/verify happy + sad paths via direct handler
     invocation (no HTTP — keeps tests hermetic when FastAPI/httpx is
     unavailable)
  5. /users/me + /tenants/{id} authentication + authorization
  6. Shared auth middleware: token kind dispatch + JWKS cache TTL

Standalone runner — no pytest required.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import base64

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.normpath(os.path.join(HERE, ".."))
SERVICES = os.path.normpath(os.path.join(SVC, ".."))
ROOT = os.path.normpath(os.path.join(SVC, "..", ".."))
sys.path.insert(0, SVC)
sys.path.insert(0, SERVICES)
sys.path.insert(0, ROOT)

from store import (                                       # noqa: E402
    IdentityStore, generate_otp_code,
    generate_invite_code, normalize_invite_code,
)
from jwt_issuer import (                                  # noqa: E402
    issue_internal_jwt, verify_internal_jwt,
)
from auth0_client import StubAuth0OtpClient                # noqa: E402
from dsar import DsarClient, ServiceEntry                  # noqa: E402
from _shared.audit import AuditLog, compute_content_hash   # noqa: E402


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
# 1. IdentityStore
# ====================================================================
section("1. IdentityStore — tenants / users / consent / sessions / OTP")

s = IdentityStore()
pilot = s.get_tenant("tnt_pilot_mx")
expect("pilot tenant seeded",                 pilot is not None and pilot.name.startswith("Casa"))

u1 = s.upsert_user_by_phone(tenant_id="tnt_pilot_mx",
                            phone_e164="+525540001234",
                            auth0_subject="sms|abc")
expect("upsert creates a user",               u1 is not None and u1.id.startswith("u_"))
expect("upsert returns same user on retry",
       s.upsert_user_by_phone(tenant_id="tnt_pilot_mx",
                              phone_e164="+525540001234").id == u1.id)
expect("get_user_by_phone hits",              s.get_user_by_phone("+525540001234") is not None)
expect("user.public() omits auth0_subject",
       "auth0_subject" not in u1.public())

cr1 = s.record_consent(user_id=u1.id, tenant_id=u1.tenant_id,
                       purpose="lfpdppp", granted=True,
                       text="aviso v1")
expect("consent record created",              cr1.id.startswith("cns_"))
expect("text_hash is a hex digest",
       len(cr1.text_hash) == 64 and all(c in "0123456789abcdef" for c in cr1.text_hash))
expect("active consent reflected",            s.has_active_consent(u1.id, "lfpdppp"))
n = s.revoke_consent(u1.id, "lfpdppp")
expect("revoke flips active to false",        n == 1 and not s.has_active_consent(u1.id, "lfpdppp"))

sess = s.create_session(user_id=u1.id, tenant_id=u1.tenant_id)
expect("session has sess_ prefix",            sess.id.startswith("sess_"))
expect("session touchable",                   s.touch_session(sess.id) is not None)
expect("session revocable",                   s.revoke_session(sess.id))
expect("revoked session not touchable",       s.touch_session(sess.id) is None)

# OTP roundtrip
ch = s.create_otp_challenge(phone_e164="+525540001234", code="000000",
                            ttl_seconds=60)
ok, why = s.verify_otp(ch.id, "999999")
expect("wrong code rejected",                 not ok and why == "bad_code")
ok, why = s.verify_otp(ch.id, "000000")
expect("right code accepted",                 ok and why == "ok")
ok, why = s.verify_otp(ch.id, "000000")
expect("already_used on second attempt",      not ok and why == "already_used")

# Expiry
ch2 = s.create_otp_challenge(phone_e164="+525540001234", code="123456",
                             ttl_seconds=0)
# bypass ttl
ch2.expires_at = time.time() - 1
ok, why = s.verify_otp(ch2.id, "123456")
expect("expired challenge rejected",          not ok and why == "expired")

# Lockout after 5 wrong attempts (verify_otp counts attempts even on
# wrong codes, and rejects with too_many_attempts after the 5th try).
ch3 = s.create_otp_challenge(phone_e164="+525540001234", code="654321")
for _ in range(5):
    s.verify_otp(ch3.id, "111111")
ok, why = s.verify_otp(ch3.id, "654321")
expect("locked after 5 failures",             not ok and why == "too_many_attempts")

# generate_otp_code shape
code = generate_otp_code(6)
expect("OTP is 6-digit numeric",              len(code) == 6 and code.isdigit())
expect("OTP avoids leading zeros",            code[0] != "0")


# ====================================================================
# 2. JWT issuer / verifier
# ====================================================================
section("2. jwt_issuer — HS256 round-trip + tamper detection")

SECRET = "test-secret-32chars-min-please-ok"
tok = issue_internal_jwt(
    tenant_id="tnt_pilot_mx", user_id="u_abc",
    session_id="sess_xyz", role="buyer", secret=SECRET, ttl_seconds=10,
)
claims = verify_internal_jwt(tok, secret=SECRET)
expect("round-trip yields tenant_id",         claims["tenant_id"] == "tnt_pilot_mx")
expect("round-trip yields user_id",           claims["user_id"] == "u_abc")
expect("round-trip yields role",              claims["role"] == "buyer")
expect("round-trip yields sid",               claims["sid"] == "sess_xyz")

# Tamper
parts = tok.split(".")
# Flip a bit in the payload
bad_payload = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
tampered = parts[0] + "." + bad_payload + "." + parts[2]
try:
    verify_internal_jwt(tampered, secret=SECRET)
    expect("tampered payload rejected",       False, "did not raise")
except ValueError as e:
    expect("tampered payload rejected",       "bad signature" in str(e), str(e))

# Wrong secret
try:
    verify_internal_jwt(tok, secret="wrong-secret-wrong-secret-wrong")
    expect("wrong secret rejected",           False, "did not raise")
except ValueError as e:
    expect("wrong secret rejected",           "bad signature" in str(e), str(e))

# Expired
expired = issue_internal_jwt(
    tenant_id="t", user_id="u", session_id="s",
    secret=SECRET, ttl_seconds=-100,
)
try:
    verify_internal_jwt(expired, secret=SECRET, leeway_seconds=1)
    expect("expired token rejected",          False, "did not raise")
except ValueError as e:
    expect("expired token rejected",          "expired" in str(e), str(e))

# Wrong audience
try:
    verify_internal_jwt(tok, secret=SECRET, audience="other-audience")
    expect("wrong audience rejected",         False, "did not raise")
except ValueError as e:
    expect("wrong audience rejected",         "bad audience" in str(e), str(e))

# Wrong issuer
try:
    verify_internal_jwt(tok, secret=SECRET, issuer="other-issuer")
    expect("wrong issuer rejected",           False, "did not raise")
except ValueError as e:
    expect("wrong issuer rejected",           "bad issuer" in str(e), str(e))

# Malformed
try:
    verify_internal_jwt("not.a.jwt", secret=SECRET)
    expect("malformed token rejected",        False, "did not raise")
except ValueError:
    expect("malformed token rejected",        True)


# ====================================================================
# 3. StubAuth0OtpClient
# ====================================================================
section("3. StubAuth0OtpClient")

async def _stub_flow():
    c = StubAuth0OtpClient(fixed_code="000000")
    await c.start(phone_e164="+525540001234")
    return c, await c.verify(phone_e164="+525540001234", otp="000000")


client, tokens = asyncio.run(_stub_flow())
expect("stub recorded the start call",        len(client.started) == 1
       and client.started[0]["phone_e164"] == "+525540001234")
expect("stub returns subject prefixed sms|stub-",
       tokens.subject.startswith("sms|stub-"))
expect("stub returns id_token + access_token",
       tokens.id_token.startswith("stub-id-")
       and tokens.access_token.startswith("stub-access-"))

# Wrong code on stub
async def _stub_bad():
    c = StubAuth0OtpClient()
    try:
        await c.verify(phone_e164="+1", otp="999999")
        return None
    except RuntimeError as e:
        return str(e)


err = asyncio.run(_stub_bad())
expect("stub rejects wrong code",             err is not None and "wrong otp" in err)


# ====================================================================
# 4. Handler-level flow — /auth/start + /auth/verify
#
# We import main only if FastAPI is available; otherwise the structural
# import path is exercised via a stubbed pydantic-free shim.
# ====================================================================
section("4. Handler-level /auth/start + /auth/verify")

try:
    import fastapi  # noqa: F401
    HAS_FASTAPI = True
except Exception:
    HAS_FASTAPI = False

if HAS_FASTAPI:
    os.environ["IDENTITY_EXPOSE_DEV_CODE"] = "1"
    # Section 4 exercises the OTP flow in isolation; the closed-beta
    # invite gate has its own sections (9–10) below.
    os.environ["IDENTITY_INVITE_REQUIRED"] = "0"
    import importlib
    import main as identity_main
    importlib.reload(identity_main)

    test_store = IdentityStore()
    test_auth0 = StubAuth0OtpClient(fixed_code="246810")
    identity_main.set_dependencies(store=test_store, auth0=test_auth0)

    async def _full_flow():
        start = await identity_main.auth_start(
            identity_main.AuthStartRequest(phone_e164="+525541112233"),
        )
        verify = await identity_main.auth_verify(
            identity_main.AuthVerifyRequest(
                phone_e164="+525541112233",
                challenge_id=start.challenge_id,
                code="246810",
            ),
        )
        return start, verify

    start_resp, verify_resp = asyncio.run(_full_flow())
    expect("start yields challenge_id",       start_resp.challenge_id.startswith("otp_"))
    expect("start exposes dev_code in dev mode", start_resp.dev_code == "246810")
    expect("verify returns a non-empty token", bool(verify_resp.access_token))
    expect("verify carries user.public()",
           "phone_e164" in verify_resp.user
           and "auth0_subject" not in verify_resp.user)
    expect("verify returns tenant",           verify_resp.tenant["id"] == "tnt_pilot_mx")

    # The returned JWT should verify with the issuer's defaults.
    claims = verify_internal_jwt(verify_resp.access_token)
    expect("issued token validates as internal HS256",
           claims["tenant_id"] == "tnt_pilot_mx"
           and claims["user_id"].startswith("u_"))

    # Bad OTP on verify
    async def _bad_otp():
        try:
            return await identity_main.auth_verify(
                identity_main.AuthVerifyRequest(
                    phone_e164="+525541112233",
                    challenge_id=start_resp.challenge_id,
                    code="999999",
                ),
            )
        except identity_main.HTTPException as e:
            return e.status_code, e.detail

    rc = asyncio.run(_bad_otp())
    expect("bad OTP returns 401",
           isinstance(rc, tuple) and rc[0] == 401
           and rc[1] in ("already_used", "bad_code"))

    # /users/me with the freshly-issued token
    async def _users_me():
        class _Req:
            headers = {"Authorization": f"Bearer {verify_resp.access_token}"}
        # The Depends() expansion is replicated manually here.
        claims = await identity_main._authenticated(_Req())  # noqa: SLF001
        return await identity_main.users_me(claims=claims)

    me = asyncio.run(_users_me())
    expect("/users/me returns the user dict",
           "user" in me and me["user"]["phone_e164"] == "+525541112233")
    expect("/users/me returns the tenant",    me["tenant"]["id"] == "tnt_pilot_mx")
    expect("/users/me returns consents (empty here)",
           isinstance(me["consents"], list))

    # /tenants/{id} — own tenant
    async def _tenant_self():
        class _Req:
            headers = {"Authorization": f"Bearer {verify_resp.access_token}"}
        claims = await identity_main._authenticated(_Req())  # noqa: SLF001
        return await identity_main.tenants_get("tnt_pilot_mx", claims=claims)

    t = asyncio.run(_tenant_self())
    expect("/tenants/own returns the tenant", t["id"] == "tnt_pilot_mx")

    # /tenants/{id} — foreign tenant (forbidden)
    async def _tenant_foreign():
        class _Req:
            headers = {"Authorization": f"Bearer {verify_resp.access_token}"}
        claims = await identity_main._authenticated(_Req())  # noqa: SLF001
        try:
            return await identity_main.tenants_get("tnt_other_org", claims=claims)
        except identity_main.HTTPException as e:
            return e.status_code

    rc = asyncio.run(_tenant_foreign())
    expect("/tenants/foreign returns 403",    rc == 403)

    # Missing token
    async def _no_token():
        class _Req:
            headers = {}
        try:
            await identity_main._authenticated(_Req())  # noqa: SLF001
        except identity_main.HTTPException as e:
            return e.status_code

    rc = asyncio.run(_no_token())
    expect("missing token returns 401",       rc == 401)
else:
    print("  (fastapi not installed; skipping handler-level checks)")


# ====================================================================
# 5. Shared auth middleware — token-kind dispatch
# ====================================================================
section("5. Shared auth_middleware — verify_any_token + JwksCache TTL")

# Path-juggling: we want to import the shared middleware from
# services/_shared but we already added the service dir to sys.path.
sys.path.insert(0, os.path.normpath(os.path.join(ROOT)))
try:
    from services._shared.auth_middleware import (  # type: ignore  # noqa: E402
        verify_any_token, JwksCache, install_jwks_cache,
    )
    HAS_SHARED = True
except Exception as e:
    print(f"  (could not import shared middleware: {e!r}; skipping)")
    HAS_SHARED = False

if HAS_SHARED:
    SECRET2 = "shared-mw-test-secret-pls-32+chars"
    os.environ["IDENTITY_JWT_SECRET"] = SECRET2
    import importlib
    # P4.6: the canonical lives in services/_shared/internal_jwt.py;
    # reload it so INTERNAL_HS_SECRET re-reads the env. Then reload the
    # local shim AND the auth_middleware so every reference to
    # verify_internal_jwt picks up the refreshed canonical surface
    # (Python caches function `__globals__` per-module — reload of one
    # module doesn't refresh references held elsewhere).
    try:
        import internal_jwt as _canonical
        importlib.reload(_canonical)
    except Exception:
        pass
    import jwt_issuer as _ji
    importlib.reload(_ji)
    import services._shared.auth_middleware as _amw   # type: ignore  # noqa: E402
    importlib.reload(_amw)
    verify_any_token = _amw.verify_any_token          # re-bind to refreshed
    JwksCache = _amw.JwksCache
    install_jwks_cache = _amw.install_jwks_cache
    # Re-bind the top-level helpers too: later sections (8, 10) sign
    # tokens that the reloaded `main` (which now verifies with SECRET2)
    # must accept. The stale bindings still carry the original secret.
    issue_internal_jwt = _ji.issue_internal_jwt       # noqa: F811
    verify_internal_jwt = _ji.verify_internal_jwt     # noqa: F811

    tok2 = _ji.issue_internal_jwt(
        tenant_id="tnt_x", user_id="u_y",
        session_id="sess_z", role="buyer",
    )

    async def _verify_hs():
        return await verify_any_token(tok2)

    out = asyncio.run(_verify_hs())
    expect("verify_any_token routes HS256",
           out["tenant_id"] == "tnt_x" and out["user_id"] == "u_y")

    # Bad-alg token
    bad_header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    payload_b = base64.urlsafe_b64encode(b"{}").rstrip(b"=").decode("ascii")
    bad = f"{bad_header}.{payload_b}.AA"

    async def _verify_bad():
        try:
            await verify_any_token(bad)
            return None
        except ValueError as e:
            return str(e)

    err = asyncio.run(_verify_bad())
    expect("verify_any_token rejects alg=none",
           err is not None and "unsupported alg" in err)

    # JwksCache TTL — first call fetches, second within TTL uses cache.
    t0 = [1000.0]
    fetched = {"n": 0}

    async def _fetcher():
        fetched["n"] += 1
        return {"keys": [{"kid": "k1", "kty": "RSA"}]}

    cache = JwksCache(fetcher=_fetcher, ttl_seconds=60,
                      now_fn=lambda: t0[0])
    asyncio.run(cache.get("k1"))
    asyncio.run(cache.get("k1"))
    expect("JwksCache caches within TTL",     fetched["n"] == 1, str(fetched))

    t0[0] += 120
    asyncio.run(cache.get("k1"))
    expect("JwksCache refreshes after TTL",   fetched["n"] == 2, str(fetched))


# ====================================================================
# 6. AuditLog — hash-chain integrity (P4.2)
# ====================================================================
section("6. AuditLog — append / verify_chain / tamper detection")

log = AuditLog(now_fn=lambda: 1700000000.0)
e1 = log.append(actor="identity", action="auth.start",
                tenant_id="t1", target="otp_a",
                detail={"x": 1})
e2 = log.append(actor="identity", action="auth.verify",
                tenant_id="t1", user_id="u1",
                target="sess_a", detail={"y": 2})
e3 = log.append(actor="identity", action="consent.grant",
                tenant_id="t1", user_id="u1",
                target="cns_a", detail={"purpose": "lfpdppp"})

expect("ids assigned sequentially",          [e1.id, e2.id, e3.id] == [1, 2, 3])
expect("first entry has no prev_hash",       e1.prev_hash is None)
expect("second entry links to first",        e2.prev_hash == e1.content_hash)
expect("third entry links to second",        e3.prev_hash == e2.content_hash)
expect("content_hash is 64 hex chars",
       all(len(e.content_hash) == 64 for e in (e1, e2, e3)))

ok, reason = log.verify_chain()
expect("chain verifies clean",               ok and reason == "ok", reason)

# Tamper: flip a detail value
log._entries[1].detail = {"y": 999}  # noqa: SLF001
ok, reason = log.verify_chain()
expect("tampered detail detected",           not ok and "id=2" in reason, reason)

# Restore + check filter
log._entries[1].detail = {"y": 2}  # noqa: SLF001
log._entries[1].content_hash = compute_content_hash(log._entries[1])  # noqa: SLF001
# Re-link e3 since e2's hash changed (we rewrote it identically here so prev is identical)
expect("filter by user_id works",
       len(log.filter(user_id="u1")) == 2)
expect("filter by action_prefix works",
       len(log.filter(action_prefix="consent.")) == 1)
expect("filter by tenant_id works",
       len(log.filter(tenant_id="t1")) == 3)
expect("filter unknown tenant is empty",
       len(log.filter(tenant_id="t_nope")) == 0)


# ====================================================================
# 7. DSAR fan-out — export ZIP + delete receipts (with mocked HTTP)
# ====================================================================
section("7. DSAR fan-out — export + delete")

class _MockResp:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body
    def json(self): return self._body
    @property
    def content(self): return json.dumps(self._body).encode("utf-8")


class _MockHttpClient:
    def __init__(self, route_table: dict):
        self.calls: list[tuple[str, str, dict]] = []
        self._table = route_table
        self.closed = False
    async def get(self, url, *, headers, timeout):
        self.calls.append(("GET", url, dict(headers)))
        key = ("GET", url)
        if key not in self._table:
            return _MockResp(404, {"error": "not found"})
        return self._table[key]
    async def delete(self, url, *, headers, timeout):
        self.calls.append(("DELETE", url, dict(headers)))
        key = ("DELETE", url)
        if key not in self._table:
            return _MockResp(404, {"error": "not found"})
        return self._table[key]
    async def aclose(self):
        self.closed = True


REGISTRY = [
    ServiceEntry("listings",   "http://listings:8003"),
    ServiceEntry("scheduling", "http://scheduling:8004"),
    ServiceEntry("documents",  "http://documents:8005"),
]
USER_ID = "u_test_dsar"
TENANT_ID = "tnt_pilot_mx"


def _export_table(uid: str = USER_ID) -> dict:
    return {
        ("GET", f"http://listings:8003/dsar/user/{uid}"):
            _MockResp(200, {"listings_saved": [{"id": "L-1"}]}),
        ("GET", f"http://scheduling:8004/dsar/user/{uid}"):
            _MockResp(200, {"slots_booked": []}),
        # documents fails — receipt must record it without aborting
        ("GET", f"http://documents:8005/dsar/user/{uid}"):
            _MockResp(500, {"error": "boom"}),
    }


async def _export_run():
    table = _export_table()
    fake_http = _MockHttpClient(table)
    client = DsarClient(
        registry=REGISTRY,
        http_factory=lambda: fake_http,
        internal_token="test-token",
    )
    return await client.export_user(USER_ID, TENANT_ID), fake_http


(zip_bytes, receipts), fake_http = asyncio.run(_export_run())
expect("DSAR fired 3 GETs",                  len([c for c in fake_http.calls if c[0] == "GET"]) == 3)
expect("DSAR includes Bearer header",
       all("Authorization" in c[2] and c[2]["Authorization"].startswith("Bearer ")
           for c in fake_http.calls),
       str(fake_http.calls[0][2]))
expect("DSAR includes X-Tenant-Id header",
       all(c[2].get("X-Tenant-Id") == TENANT_ID for c in fake_http.calls),
       "")
expect("3 receipts produced",                len(receipts) == 3)
expect("listings receipt success",
       receipts[0].service == "listings" and receipts[0].status == 200
       and receipts[0].error is None)
expect("documents receipt records 500",
       receipts[2].service == "documents" and receipts[2].status == 500
       and receipts[2].error == "HTTP 500")

import zipfile as _zf
import io as _io
zf = _zf.ZipFile(_io.BytesIO(zip_bytes), mode="r")
names = sorted(zf.namelist())
expect("ZIP contains per-service files + manifest",
       names == sorted(["listings.json", "scheduling.json",
                        "documents.json", "manifest.json"]),
       str(names))

manifest = json.loads(zf.read("manifest.json"))
expect("manifest user_id matches",           manifest["user_id"] == USER_ID)
expect("manifest tenant_id matches",         manifest["tenant_id"] == TENANT_ID)
expect("manifest summary counts",
       manifest["summary"]["successful"] == 2
       and manifest["summary"]["failed"] == 1,
       str(manifest["summary"]))
expect("manifest lists every service",       len(manifest["services"]) == 3)
expect("each service sha256 is 64 hex",
       all(len(s["sha256"]) == 64 for s in manifest["services"]),
       str(manifest["services"]))


def _delete_table(uid: str = USER_ID) -> dict:
    return {
        ("DELETE", f"http://listings:8003/dsar/user/{uid}"):
            _MockResp(200, {"deleted": True, "count": 5}),
        ("DELETE", f"http://scheduling:8004/dsar/user/{uid}"):
            _MockResp(204, None),
        ("DELETE", f"http://documents:8005/dsar/user/{uid}"):
            _MockResp(0, None),
    }


async def _delete_run():
    table = _delete_table()
    fake_http = _MockHttpClient(table)
    client = DsarClient(
        registry=REGISTRY,
        http_factory=lambda: fake_http,
        internal_token="test-token",
    )
    return await client.delete_user(USER_ID, TENANT_ID), fake_http


delete_receipts, fake_http_d = asyncio.run(_delete_run())
expect("DSAR delete fired 3 DELETEs",
       len([c for c in fake_http_d.calls if c[0] == "DELETE"]) == 3)
expect("listings deletion success + count=5",
       delete_receipts[0].deleted and delete_receipts[0].count == 5)
expect("scheduling 204 also counts as deleted",
       delete_receipts[1].deleted and delete_receipts[1].status == 204)
expect("documents 0/error recorded as not deleted",
       not delete_receipts[2].deleted and delete_receipts[2].error is not None)


# ====================================================================
# 8. Consent + DSAR handlers (FastAPI required)
# ====================================================================
section("8. /consent + /dsar/export + /dsar/delete handlers")

if HAS_FASTAPI:
    import importlib
    import main as identity_main
    importlib.reload(identity_main)

    test_store = IdentityStore()
    test_audit = AuditLog(now_fn=lambda: 1700000000.0)
    # Create user via direct upsert so we can sign a token for them.
    user = test_store.upsert_user_by_phone(
        tenant_id="tnt_pilot_mx",
        phone_e164="+525540009999",
        auth0_subject="sms|test",
    )
    sess = test_store.create_session(user_id=user.id,
                                     tenant_id=user.tenant_id)
    tok = issue_internal_jwt(
        tenant_id=user.tenant_id, user_id=user.id,
        session_id=sess.id, role="buyer",
    )
    # DSAR client backed by a deterministic fake HTTP. Tables are keyed
    # on the handler-authenticated user's id (random per run), not the
    # fixed USER_ID used by the unit-level sections above.
    fake_http_h = _MockHttpClient(_export_table(user.id))
    fake_http_h2 = _MockHttpClient(_delete_table(user.id))
    flip = {"is_delete": False}

    class _ToggleHttp:
        def __init__(self):
            self.calls = []
        async def get(self, url, *, headers, timeout):
            return await fake_http_h.get(url, headers=headers, timeout=timeout)
        async def delete(self, url, *, headers, timeout):
            return await fake_http_h2.delete(url, headers=headers, timeout=timeout)
        async def aclose(self): pass

    fake_http_dsar = _ToggleHttp()
    dsar_client = DsarClient(
        registry=REGISTRY,
        http_factory=lambda: fake_http_dsar,
        internal_token="test-token",
    )
    identity_main.set_dependencies(
        store=test_store, audit=test_audit, dsar=dsar_client,
        auth0=StubAuth0OtpClient(),
    )

    class _Req:
        def __init__(self, token): self.headers = {"Authorization": f"Bearer {token}"}

    # ---- /consent ----
    async def _grant():
        claims = await identity_main._authenticated(_Req(tok))  # noqa: SLF001
        return await identity_main.consent_record(
            identity_main.ConsentRecordRequest(
                purpose="lfpdppp", granted=True,
                text_version="aviso-v1",
                text_sha256="a" * 64,
                channel="voice",
            ),
            claims=claims,
        )

    cr = asyncio.run(_grant())
    expect("/consent returns id",            cr.id.startswith("cns_") and cr.granted)
    expect("audit recorded consent.grant",
           any(e.action == "consent.grant" for e in test_audit.all()))

    # ---- /consent/revoke ----
    async def _revoke():
        claims = await identity_main._authenticated(_Req(tok))  # noqa: SLF001
        return await identity_main.consent_revoke(
            identity_main.ConsentRevokeRequest(purpose="lfpdppp"),
            claims=claims,
        )

    rv = asyncio.run(_revoke())
    expect("/consent/revoke returns count",  rv["revoked"] == 1, str(rv))
    expect("audit recorded consent.revoke",
           any(e.action == "consent.revoke" for e in test_audit.all()))

    # ---- /consent (list) ----
    async def _list():
        claims = await identity_main._authenticated(_Req(tok))  # noqa: SLF001
        return await identity_main.consent_list(claims=claims)

    lst = asyncio.run(_list())
    expect("/consent list contains revoked entry",
           len(lst["consents"]) == 1 and lst["consents"][0]["revoked"] is True,
           str(lst))

    # ---- /dsar/export ----
    async def _export():
        claims = await identity_main._authenticated(_Req(tok))  # noqa: SLF001
        return await identity_main.dsar_export(claims=claims)

    resp = asyncio.run(_export())
    expect("/dsar/export returns application/zip",
           resp.media_type == "application/zip")
    expect("/dsar/export ZIP is non-trivial",
           len(resp.body) > 200, f"len={len(resp.body)}")
    expect("/dsar/export sets services-successful header",
           resp.headers.get("X-DSAR-Services-Successful") == "2",
           str(dict(resp.headers)))
    expect("audit recorded dsar.export",
           any(e.action == "dsar.export" for e in test_audit.all()))

    # ---- /dsar/delete ----
    async def _delete():
        claims = await identity_main._authenticated(_Req(tok))  # noqa: SLF001
        return await identity_main.dsar_delete(claims=claims)

    dout = asyncio.run(_delete())
    expect("/dsar/delete returns per-service list",
           len(dout["services"]) == 3, str(dout))
    expect("/dsar/delete revoked sessions",  dout["sessions_revoked"] >= 1)
    expect("audit recorded dsar.delete",
           any(e.action == "dsar.delete" for e in test_audit.all()))
    expect("post-DSAR audit chain still valid",
           test_audit.verify_chain()[0],
           test_audit.verify_chain()[1])


# ====================================================================
# 9. Invite codes — generation + lifecycle (P4.4)
# ====================================================================
section("9. InviteCode — generate / normalize / validate / redeem")

# Generator format + alphabet
code = generate_invite_code()
expect("generated code is XXXX-XXXX format",
       len(code) == 9 and code[4] == "-"
       and code[:4].isalnum() and code[5:].isalnum(),
       code)
expect("generated code uses Crockford-style alphabet (no 0/O/1/I/L/U)",
       not any(c in code for c in "01ILOU"), code)

# Normalize forgives lower-case / missing dash / spaces
for raw, want in [
    ("abcd-1234", "ABCD-1234"),
    ("ABCD1234",  "ABCD-1234"),
    ("  abcd 1234 ", "ABCD-1234"),
    ("ABCD-1234",  "ABCD-1234"),
]:
    got = normalize_invite_code(raw)
    expect(f"normalize({raw!r}) → {want}", got == want, got)

# 1000-trial collision sanity (won't hit, just verify uniqueness).
codes = {generate_invite_code() for _ in range(1000)}
expect("1000 generated codes are all unique", len(codes) == 1000,
       f"got {len(codes)}")

# Store roundtrip
clock = [1700000000.0]
s2 = IdentityStore(now_fn=lambda: clock[0])
ic = s2.create_invite_code(
    tenant_id="tnt_pilot_mx", label="may-001",
    role="buyer", ttl_seconds=3600,
)
expect("create_invite_code returns InviteCode with code",
       ic.code and ic.label == "may-001" and ic.tenant_id == "tnt_pilot_mx")
expect("get_invite_code is case-insensitive",
       s2.get_invite_code(ic.code.lower()) is ic)

ok_v, reason_v, ic_v = s2.validate_invite_code(ic.code)
expect("validate fresh code returns ok",     ok_v and reason_v == "ok")

ok_v, reason_v, _ = s2.validate_invite_code("XXXX-XXXX")
expect("validate unknown code → unknown_code",
       not ok_v and reason_v == "unknown_code")

# Redeem
ok_r, reason_r, ic_r = s2.redeem_invite_code(
    ic.code, user_id="u_abc", phone_e164="+525540001234",
)
expect("redeem succeeds",                    ok_r and reason_r == "ok"
       and ic_r is not None
       and ic_r.redeemed_by_user_id == "u_abc")
expect("redeemed_at set after redeem",       ic.redeemed_at is not None)

# Second redeem fails
ok_r, reason_r, _ = s2.redeem_invite_code(
    ic.code, user_id="u_def", phone_e164="+5555",
)
expect("double-redeem → already_redeemed",
       not ok_r and reason_r == "already_redeemed")

# Expiry
ic2 = s2.create_invite_code(
    tenant_id="tnt_pilot_mx", label="exp-001",
    ttl_seconds=10,
)
clock[0] += 100
ok_v, reason_v, _ = s2.validate_invite_code(ic2.code)
expect("expired code → expired",             not ok_v and reason_v == "expired")

# List filters
s2.create_invite_code(tenant_id="tnt_pilot_mx", label="o-1", role="buyer")
s2.create_invite_code(tenant_id="tnt_other",    label="x-1", role="buyer")
all_pilot = s2.list_invite_codes(tenant_id="tnt_pilot_mx")
expect("list filters by tenant",             len(all_pilot) >= 3
       and all(i.tenant_id == "tnt_pilot_mx" for i in all_pilot))
open_pilot = s2.list_invite_codes(tenant_id="tnt_pilot_mx", redeemed=False)
expect("list redeemed=False excludes redeemed",
       all(i.redeemed_at is None for i in open_pilot))

# Code collision
try:
    s2.create_invite_code(tenant_id="tnt_pilot_mx", label="dup",
                          code=ic.code)
    expect("create with existing code raises", False, "did not raise")
except ValueError as e:
    expect("create with existing code raises ValueError",
           "collision" in str(e), str(e))


# ====================================================================
# 10. Invite handlers — /auth/invite/validate + /admin/invites/batch (FastAPI required)
# ====================================================================
section("10. /auth/invite/validate + /admin/invites/batch handlers")

if HAS_FASTAPI:
    os.environ["IDENTITY_INVITE_REQUIRED"] = "1"
    os.environ["IDENTITY_EXPOSE_DEV_CODE"] = "1"
    import importlib
    import main as identity_main
    importlib.reload(identity_main)

    test_store = IdentityStore()
    test_audit = AuditLog(now_fn=lambda: 1700000000.0)
    identity_main.set_dependencies(
        store=test_store, audit=test_audit,
        auth0=StubAuth0OtpClient(fixed_code="111222"),
    )

    # 1. Public validate — unknown code
    async def _validate_unknown():
        return await identity_main.auth_invite_validate(
            identity_main.InviteValidateRequest(code="XXXX-XXXX"),
        )
    r = asyncio.run(_validate_unknown())
    expect("invalidate validate unknown → ok=False",
           (not r.ok) and r.reason == "unknown_code")

    # 2. Public validate — fresh code
    ic = test_store.create_invite_code(
        tenant_id="tnt_pilot_mx", label="beta-w1", role="buyer",
    )
    async def _validate_ok():
        return await identity_main.auth_invite_validate(
            identity_main.InviteValidateRequest(code=ic.code),
        )
    r = asyncio.run(_validate_ok())
    expect("validate fresh code → ok=True + label",
           r.ok and r.label == "beta-w1" and r.tenant_id == "tnt_pilot_mx")

    # 3. /auth/start requires invite when INVITE_REQUIRED=1
    async def _start_no_invite():
        try:
            await identity_main.auth_start(identity_main.AuthStartRequest(
                phone_e164="+525540009999",
            ))
        except identity_main.HTTPException as e:
            return e.status_code, e.detail
    rc = asyncio.run(_start_no_invite())
    expect("/auth/start without invite → 400 invite_required",
           rc == (400, "invite_required"))

    # 4. Full flow with invite: start + verify + redeem
    async def _full_with_invite():
        start = await identity_main.auth_start(identity_main.AuthStartRequest(
            phone_e164="+525540009999", invite_code=ic.code,
        ))
        verify = await identity_main.auth_verify(identity_main.AuthVerifyRequest(
            phone_e164="+525540009999",
            challenge_id=start.challenge_id,
            code="111222",
            invite_code=ic.code,
        ))
        return start, verify

    start_r, verify_r = asyncio.run(_full_with_invite())
    expect("/auth/start echoes invite_label",
           start_r.invite_label == "beta-w1")
    expect("/auth/start echoes tenant_id",
           start_r.tenant_id == "tnt_pilot_mx")
    expect("/auth/verify mints token", bool(verify_r.access_token))
    # Code is now redeemed
    ok_v, reason_v, _ = test_store.validate_invite_code(ic.code)
    expect("invite is now redeemed",
           not ok_v and reason_v == "already_redeemed")
    # Audit shows the redemption
    actions = [e.action for e in test_audit.all()]
    expect("audit captured invite.redeem",   "invite.redeem" in actions, str(actions))

    # 5. Replay: second verify with same redeemed code → 409
    async def _replay():
        # Start fresh challenge first.
        start = await identity_main.auth_start(identity_main.AuthStartRequest(
            phone_e164="+525540008888", invite_code=ic.code,
        ))
        return start
    rc = None
    try:
        asyncio.run(_replay())
    except identity_main.HTTPException as e:
        rc = (e.status_code, e.detail)
    expect("re-use redeemed invite at /auth/start → 400 invite:already_redeemed",
           rc == (400, "invite:already_redeemed"))

    # 6. Admin batch generation
    # Mint an admin token by direct token issuance.
    admin_user = test_store.upsert_user_by_phone(
        tenant_id="tnt_pilot_mx", phone_e164="+525500000000", role="admin",
    )
    admin_user.role = "admin"
    admin_sess = test_store.create_session(user_id=admin_user.id,
                                           tenant_id=admin_user.tenant_id)
    admin_tok = issue_internal_jwt(
        tenant_id=admin_user.tenant_id, user_id=admin_user.id,
        session_id=admin_sess.id, role="admin",
    )

    class _AdminReq:
        headers = {"Authorization": f"Bearer {admin_tok}"}

    async def _admin_batch():
        claims = await identity_main._authenticated(_AdminReq())  # noqa: SLF001
        return await identity_main.admin_invite_batch(
            identity_main.InviteBatchRequest(
                tenant_id="tnt_pilot_mx",
                count=5, label_prefix="batch-test", role="buyer",
            ),
            claims=claims,
        )

    batch = asyncio.run(_admin_batch())
    expect("admin batch returns 5 codes",       batch["count"] == 5
           and len(batch["codes"]) == 5)
    expect("batch labels follow prefix-NNN",
           batch["codes"][0]["label"].startswith("batch-test-")
           and batch["codes"][-1]["label"].endswith("-005"))
    actions = [e.action for e in test_audit.all()]
    expect("audit captured invite.batch_create",
           "invite.batch_create" in actions, str(actions))


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
print("  All identity assertions green. ✅")
sys.exit(0)
