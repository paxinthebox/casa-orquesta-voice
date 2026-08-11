"""
P4.6 — Shared DSAR responder tests + cross-service wiring guard.

Two layers:

  1. Hermetic unit tests for `dsar_responder`:
       * build_export_envelope shape
       * build_delete_receipt shape
       * _check_tenant_scope cross-user rejection
       * _check_tenant_scope admin bypass
       * `mount_dsar` end-to-end via a tiny in-process FastAPI app
         (only runs when FastAPI is installed; skips cleanly otherwise)

  2. Structural cross-service guard:
     Every concrete service main.py must import auth_middleware +
     dsar_responder and call `mount_dsar(...)`. The grep is enough —
     if a service forgets, the gate fails.

Standalone runner. No pytest.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent
SERVICES = SHARED.parent
ROOT = SERVICES.parent

# Allow both `from dsar_responder import …` and the qualified form.
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(ROOT))

from dsar_responder import (                                  # noqa: E402
    build_export_envelope, build_delete_receipt,
    _check_tenant_scope,
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
# 1. Envelope + receipt shape
# ====================================================================
section("1. build_export_envelope + build_delete_receipt")

env = build_export_envelope(
    service_name="orchestrator",
    user_id="u_abc", tenant_id="tnt_x",
    payload={"conversations": [1, 2, 3]},
)
for k in ("service", "user_id", "tenant_id", "generated_at",
          "schema_version", "data"):
    expect(f"export envelope has {k}",         k in env)
expect("export envelope service matches",      env["service"] == "orchestrator")
expect("export envelope schema_version is 1",  env["schema_version"] == 1)
expect("export envelope data passthrough",     env["data"]["conversations"] == [1, 2, 3])

rec = build_delete_receipt(
    service_name="orchestrator",
    user_id="u_abc", tenant_id="tnt_x",
    count=7,
)
for k in ("service", "user_id", "tenant_id", "deleted", "count",
          "deleted_at", "schema_version", "detail"):
    expect(f"delete receipt has {k}",          k in rec)
expect("delete receipt count is int",          isinstance(rec["count"], int)
       and rec["count"] == 7)
expect("delete receipt deleted defaults True", rec["deleted"] is True)

rec2 = build_delete_receipt(
    service_name="documents",
    user_id="u_abc", tenant_id="tnt_x",
    count=2, deleted=False,
    detail={"reason": "retention_hold"},
)
expect("delete receipt honors deleted=False",  rec2["deleted"] is False)
expect("delete receipt passes detail",         rec2["detail"] == {"reason": "retention_hold"})


# ====================================================================
# 2. _check_tenant_scope
# ====================================================================
section("2. _check_tenant_scope — cross-user rejection + admin bypass")

try:
    from fastapi import HTTPException                          # type: ignore
    HAS_FASTAPI = True
except Exception:
    HAS_FASTAPI = False

if HAS_FASTAPI:
    # Same user → allowed (returns None silently)
    _check_tenant_scope({"user_id": "u_abc", "role": "buyer"}, "u_abc")
    expect("same user allowed",                True)

    # Different user, non-admin → 403
    try:
        _check_tenant_scope({"user_id": "u_other", "role": "buyer"}, "u_abc")
        expect("cross-user buyer → 403",       False, "did not raise")
    except HTTPException as e:
        expect("cross-user buyer → 403",       e.status_code == 403,
               str(e.detail))

    # Different user, admin → allowed
    try:
        _check_tenant_scope({"user_id": "u_other", "role": "admin"}, "u_abc")
        expect("cross-user admin allowed",     True)
    except HTTPException as e:
        expect("cross-user admin allowed",     False, str(e.detail))


# ====================================================================
# 3. mount_dsar end-to-end (FastAPI required)
# ====================================================================
section("3. mount_dsar end-to-end")

if HAS_FASTAPI:
    try:
        from fastapi import FastAPI                              # type: ignore
        from fastapi.testclient import TestClient                # type: ignore
        HAS_CLIENT = True
    except Exception:
        HAS_CLIENT = False
    if not HAS_CLIENT:
        print("  (fastapi.testclient not available; skipping HTTP roundtrip)")

    if HAS_CLIENT:
        from dsar_responder import mount_dsar                    # noqa: E402
        # Use the no-auth variant so we don't need the full AuthInjector
        # JWT plumbing in this hermetic test.
        seen: dict[str, list] = {"purge_calls": []}

        def gather(uid, tid):
            return {"items": [{"id": "X-1"}, {"id": "X-2"}],
                    "_uid_echo": uid, "_tid_echo": tid}

        def purge(uid, tid):
            seen["purge_calls"].append((uid, tid))
            return {"count": 5, "deleted": True, "detail": {"who": uid}}

        app = FastAPI()
        mount_dsar(app, gather=gather, purge=purge,
                   service_name="testsvc", require_auth=False)
        client = TestClient(app)

        r = client.get("/dsar/user/u_xyz",
                       headers={"X-Tenant-Id": "tnt_x"})
        expect("GET 200",                          r.status_code == 200,
               r.text[:200])
        j = r.json()
        expect("GET envelope service=testsvc",     j["service"] == "testsvc")
        expect("GET echoes user_id",               j["user_id"] == "u_xyz")
        expect("GET echoes tenant_id",             j["tenant_id"] == "tnt_x")
        expect("GET payload bridged",              j["data"]["_uid_echo"] == "u_xyz"
               and j["data"]["_tid_echo"] == "tnt_x")
        expect("GET payload items count",          len(j["data"]["items"]) == 2)

        r = client.delete("/dsar/user/u_xyz",
                          headers={"X-Tenant-Id": "tnt_x"})
        expect("DELETE 200",                       r.status_code == 200,
               r.text[:200])
        j = r.json()
        expect("DELETE receipt count=5",           j["count"] == 5)
        expect("DELETE receipt deleted=True",      j["deleted"] is True)
        expect("DELETE receipt detail passthrough", j["detail"] == {"who": "u_xyz"})
        expect("DELETE purge actually called once", len(seen["purge_calls"]) == 1)
        expect("purge args (user_id, tenant_id)",
               seen["purge_calls"][0] == ("u_xyz", "tnt_x"))

        # Async gather/purge also work
        async def async_gather(uid, tid):
            return {"async": True}

        async def async_purge(uid, tid):
            return {"count": 1, "deleted": True}

        app2 = FastAPI()
        mount_dsar(app2, gather=async_gather, purge=async_purge,
                   service_name="asyncsvc", require_auth=False)
        client2 = TestClient(app2)
        r = client2.get("/dsar/user/u_a")
        expect("async gather honored",             r.status_code == 200
               and r.json()["data"]["async"] is True,
               r.text[:200])
else:
    print("  (fastapi not installed; skipping mount_dsar end-to-end)")


# ====================================================================
# 4. Structural cross-service wiring guard
# ====================================================================
section("4. Cross-service wiring — every service mounts DSAR")

SERVICES_THAT_NEED_DSAR = [
    "orchestrator", "voice-gateway", "comms",
    "listings", "scheduling", "documents", "payments",
]

for svc in SERVICES_THAT_NEED_DSAR:
    main_py = SERVICES / svc / "main.py"
    if not main_py.is_file():
        expect(f"{svc}/main.py exists", False, str(main_py))
        continue
    src = main_py.read_text(encoding="utf-8")
    expect(f"{svc} imports AuthInjector",
           "from auth_middleware import AuthInjector" in src
           or "import AuthInjector" in src,
           "")
    expect(f"{svc} imports mount_dsar",
           "from dsar_responder import mount_dsar" in src
           or "import mount_dsar" in src,
           "")
    expect(f"{svc} installs AuthInjector middleware",
           re.search(r"app\.add_middleware\(\s*AuthInjector", src) is not None,
           "")
    expect(f"{svc} calls mount_dsar(",
           re.search(r"\bmount_dsar\s*\(", src) is not None,
           "")
    expect(f"{svc} sets service_name= in mount_dsar",
           re.search(rf'service_name\s*=\s*["\']{re.escape(svc)}["\']', src)
           is not None,
           "")


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
print("  All DSAR responder + wiring assertions green. ✅")
sys.exit(0)
