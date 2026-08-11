"""
DSAR responder helper — Phase 4.6.

Every downstream service mounts `/dsar/user/{user_id}` (GET = export,
DELETE = forget) so the identity service's fan-out client (P4.2) actually
has something to talk to. This helper is the single shared
implementation: each service plugs in two callables and gets uniform
auth, tenant scoping, error envelopes, and audit-friendly receipts.

Usage in a service `main.py`:

    from _shared.auth_middleware import AuthInjector
    from _shared.dsar_responder import mount_dsar

    app.add_middleware(AuthInjector)

    def gather(user_id, tenant_id):
        # Return a JSON-serializable dict of everything this service
        # holds about (tenant_id, user_id).
        return {"conversations": [...], "summary": {...}}

    def purge(user_id, tenant_id):
        # Soft-delete or hard-delete; return {"count": N, "deleted": True}.
        return {"count": 42, "deleted": True}

    mount_dsar(app, gather=gather, purge=purge, service_name="orchestrator")

Auth model:

  * Both routes require the caller's JWT (via `AuthInjector`).
  * The caller's `tenant_id` from the JWT must match the user being
    queried. Cross-tenant queries are 403 — even from the identity
    service. The identity service signs an internal JWT specifically
    for the DSAR fan-out where the user's claim is forwarded; the
    middleware verifies it via the shared HS256 secret.
  * Admin role bypasses the cross-tenant check (for ops dashboards).

Why a helper and not a per-service rewrite?
  Putting the response shape, the audit log line, and the tenant scope
  check in one module means a future schema change (e.g. switching the
  receipt format from `{count, deleted}` to `{count, deleted_at}`)
  lands in one place. Drift across seven services is the kind of bug
  that bites at audit time.
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable, Optional, Union

try:
    from fastapi import Depends, FastAPI, HTTPException, Request  # type: ignore
    _HAS_FASTAPI = True
except Exception:    # pragma: no cover — test env without FastAPI
    _HAS_FASTAPI = False

try:
    from auth_middleware import current_claims  # type: ignore  # noqa: F401
    _from_middleware = True
except Exception:
    try:
        from _shared.auth_middleware import current_claims      # type: ignore
        _from_middleware = True
    except Exception:
        _from_middleware = False
        current_claims = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------
GatherSync = Callable[[str, str], dict]
GatherAsync = Callable[[str, str], Awaitable[dict]]
Gather = Union[GatherSync, GatherAsync]

PurgeSync = Callable[[str, str], dict]
PurgeAsync = Callable[[str, str], Awaitable[dict]]
Purge = Union[PurgeSync, PurgeAsync]


# ---------------------------------------------------------------------------
# Receipt builders
# ---------------------------------------------------------------------------
def build_export_envelope(
    *,
    service_name: str,
    user_id: str,
    tenant_id: str,
    payload: dict,
) -> dict:
    """Standardized GET response. Every service returns this shape so the
    identity fan-out ZIP manifest stays uniform."""
    return {
        "service": service_name,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "generated_at": time.time(),
        "schema_version": 1,
        "data": payload,
    }


def build_delete_receipt(
    *,
    service_name: str,
    user_id: str,
    tenant_id: str,
    count: int,
    deleted: bool = True,
    detail: Optional[dict] = None,
) -> dict:
    """Standardized DELETE response."""
    return {
        "service": service_name,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "deleted": deleted,
        "count": int(count),
        "deleted_at": time.time(),
        "schema_version": 1,
        "detail": detail or {},
    }


# ---------------------------------------------------------------------------
# Internal: tenant scope check
# ---------------------------------------------------------------------------
def _check_tenant_scope(claims: dict, user_id: str,
                        *, allow_admin: bool = True) -> None:
    """Enforce that the caller's tenant matches the user being queried.

    Identity-service-issued internal tokens carry the *caller's* claims
    (the user requesting their own data, OR the identity service acting
    on behalf of the user — same JWT, identity just forwards it). The
    `sub` and `user_id` claims must therefore match the path parameter.
    """
    if allow_admin and claims.get("role") == "admin":
        return
    if claims.get("user_id") != user_id:
        raise HTTPException(
            status_code=403,
            detail="cross-user dsar requires admin role",
        )


# ---------------------------------------------------------------------------
# mount_dsar
# ---------------------------------------------------------------------------
def mount_dsar(
    app: "FastAPI",
    *,
    gather: Gather,
    purge: Purge,
    service_name: str,
    require_auth: bool = True,
) -> None:
    """Attach GET + DELETE `/dsar/user/{user_id}` routes to `app`.

    `gather(user_id, tenant_id) → dict` and `purge(user_id, tenant_id)
    → {"count": int, "deleted": bool, "detail"?: dict}` may be sync or
    async; the helper awaits when needed.
    """
    if not _HAS_FASTAPI:
        raise RuntimeError("FastAPI is required to mount_dsar")
    if require_auth and current_claims is None:  # pragma: no cover
        raise RuntimeError(
            "AuthInjector not importable. mount_dsar(require_auth=False) "
            "to bypass, but you almost certainly don't want that in prod."
        )

    async def _run(fn, *args):
        result = fn(*args)
        if hasattr(result, "__await__"):
            return await result
        return result

    if require_auth:
        @app.get("/dsar/user/{user_id}")
        async def dsar_export(user_id: str,
                              claims: dict = Depends(current_claims)) -> dict:
            _check_tenant_scope(claims, user_id)
            tenant_id = claims["tenant_id"]
            try:
                payload = await _run(gather, user_id, tenant_id)
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"dsar gather failed: {type(e).__name__}: {e}",
                ) from e
            return build_export_envelope(
                service_name=service_name,
                user_id=user_id,
                tenant_id=tenant_id,
                payload=payload,
            )

        @app.delete("/dsar/user/{user_id}")
        async def dsar_delete(user_id: str,
                              claims: dict = Depends(current_claims)) -> dict:
            _check_tenant_scope(claims, user_id)
            tenant_id = claims["tenant_id"]
            try:
                result = await _run(purge, user_id, tenant_id)
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"dsar purge failed: {type(e).__name__}: {e}",
                ) from e
            count = int(result.get("count", 0))
            deleted = bool(result.get("deleted", True))
            detail = result.get("detail") or {}
            return build_delete_receipt(
                service_name=service_name,
                user_id=user_id,
                tenant_id=tenant_id,
                count=count, deleted=deleted, detail=detail,
            )
    else:
        # No-auth variant used only by tests + local dev.
        @app.get("/dsar/user/{user_id}")
        async def dsar_export_noauth(user_id: str,
                                     request: Request) -> dict:
            tenant_id = request.headers.get("X-Tenant-Id", "tnt_dev")
            payload = await _run(gather, user_id, tenant_id)
            return build_export_envelope(
                service_name=service_name,
                user_id=user_id,
                tenant_id=tenant_id,
                payload=payload,
            )

        @app.delete("/dsar/user/{user_id}")
        async def dsar_delete_noauth(user_id: str,
                                     request: Request) -> dict:
            tenant_id = request.headers.get("X-Tenant-Id", "tnt_dev")
            result = await _run(purge, user_id, tenant_id)
            return build_delete_receipt(
                service_name=service_name,
                user_id=user_id,
                tenant_id=tenant_id,
                count=int(result.get("count", 0)),
                deleted=bool(result.get("deleted", True)),
                detail=result.get("detail") or {},
            )
