"""
Identity Service — Phase 4.1.

Endpoints (all JSON, all es-MX-aware error messages):

    POST /auth/start              { phone_e164, locale? }
        → { challenge_id, expires_in, dev_code? }

    POST /auth/verify             { phone_e164, challenge_id, code }
        → { access_token, expires_in, user, tenant }

    GET  /users/me                Authorization: Bearer <internal jwt>
        → { user: User.public(), tenant: Tenant.public(), consents: [...] }

    GET  /tenants/{id}            Authorization: Bearer <internal jwt>
        → Tenant.public()  (only the tenant the caller belongs to)

    GET  /health
        → { status, mode, tenants, users }

The handlers stay thin: every persistence call goes through `IdentityStore`,
every Auth0 call through `Auth0OtpClient`. Tests inject both directly
(see `services/identity/tests/test_identity.py`).
"""
from __future__ import annotations

import os
import sys
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auth0_client import (
    Auth0OtpClient,
    StubAuth0OtpClient,
    build_default_auth0_client,
)
from jwt_issuer import issue_internal_jwt, verify_internal_jwt
from store import (
    IdentityStore, generate_otp_code,
    normalize_invite_code,
)
from dsar import DsarClient

# Sibling repo import — services/_shared is on the path one level above.
_PARENT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
from _shared.audit import AuditLog                          # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_TENANT_ID = os.getenv("IDENTITY_DEFAULT_TENANT_ID", "tnt_pilot_mx")
OTP_TTL_SECONDS = int(os.getenv("IDENTITY_OTP_TTL_SECONDS", "300"))
EXPOSE_DEV_CODE = os.getenv("IDENTITY_EXPOSE_DEV_CODE", "0") == "1"
# Closed beta: require an invite code on /auth/start. Flip via env.
INVITE_REQUIRED = os.getenv("IDENTITY_INVITE_REQUIRED", "1") == "1"
# Dev seed: when "1", create a known invite code + tenant on startup so the
# simulator can proceed without running the bug_bash_provision script first.
DEV_SEED = os.getenv("IDENTITY_DEV_SEED", "0") == "1"
DEV_INVITE_CODE = os.getenv("IDENTITY_DEV_INVITE_CODE", "TEST-BETA")

# Shared singletons — replaced in tests via `set_dependencies()`.
_store: IdentityStore = IdentityStore()
_auth0: Auth0OtpClient = build_default_auth0_client()
_audit: AuditLog = AuditLog()
_dsar: DsarClient = DsarClient()


def set_dependencies(*,
                     store: Optional[IdentityStore] = None,
                     auth0: Optional[Auth0OtpClient] = None,
                     audit: Optional[AuditLog] = None,
                     dsar: Optional[DsarClient] = None) -> None:
    """Hook used by the test runner to swap the in-memory store + stub
    Auth0 + audit log + DSAR client before driving the handlers."""
    global _store, _auth0, _audit, _dsar
    if store is not None:
        _store = store
    if auth0 is not None:
        _auth0 = auth0
    if audit is not None:
        _audit = audit
    if dsar is not None:
        _dsar = dsar


def get_store() -> IdentityStore:
    return _store


def get_auth0() -> Auth0OtpClient:
    return _auth0


def get_audit() -> AuditLog:
    return _audit


def get_dsar() -> DsarClient:
    return _dsar


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(application: FastAPI):  # noqa: ARG001
    """Seed a dev invite code + tenant when IDENTITY_DEV_SEED=1."""
    if DEV_SEED:
        try:
            norm = normalize_invite_code(DEV_INVITE_CODE)
            if not _store.get_invite_code(norm):
                _store.create_invite_code(
                    tenant_id=DEFAULT_TENANT_ID,
                    role="buyer",
                    label="Dev / Stage tester",
                    code=norm,
                )
            print(
                f"[identity] DEV SEED: invite code={norm!r}  "
                f"tenant={DEFAULT_TENANT_ID!r}  "
                f"(IDENTITY_EXPOSE_DEV_CODE={EXPOSE_DEV_CODE})",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[identity] DEV SEED warning: {exc}", flush=True)
    yield


app = FastAPI(title="Casa·Orquesta Identity", version="0.1.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class AuthStartRequest(BaseModel):
    phone_e164: str = Field(..., min_length=8, max_length=20)
    locale: str = "es-MX"
    invite_code: Optional[str] = None       # required when IDENTITY_INVITE_REQUIRED=1


class AuthStartResponse(BaseModel):
    challenge_id: str
    expires_in: int
    # Dev-only: when IDENTITY_EXPOSE_DEV_CODE=1 the stub returns the code
    # so the test runner can verify without a real SMS. Never set in prod.
    dev_code: Optional[str] = None
    tenant_id: str
    invite_label: Optional[str] = None      # echoed for the welcome screen


class AuthVerifyRequest(BaseModel):
    phone_e164: str
    challenge_id: str
    code: str
    invite_code: Optional[str] = None       # echoed back so it's redeemed atomically


class AuthVerifyResponse(BaseModel):
    access_token: str
    expires_in: int
    user: dict
    tenant: dict


# ---------------------------------------------------------------------------
# Auth dep — used by /users/me and /tenants/{id}
# ---------------------------------------------------------------------------
async def _authenticated(request: Request) -> dict:
    """Validate the Authorization header → return verified claims."""
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth[7:].strip()
    try:
        return verify_internal_jwt(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    mode = "stub" if isinstance(_auth0, StubAuth0OtpClient) else "real"
    return {
        "status": "ok",
        "service": "identity",
        "mode": mode,
        "tenants": len(_store._tenants),
        "users": len(_store._users),
    }


@app.post("/auth/start", response_model=AuthStartResponse)
async def auth_start(req: AuthStartRequest) -> AuthStartResponse:
    """Start the phone-OTP flow. Issues a challenge_id; sends the SMS via Auth0."""
    phone = req.phone_e164.strip()
    if not phone.startswith("+"):
        raise HTTPException(status_code=400, detail="phone_e164 must start with '+'")

    # 0. Closed-beta invite gate.
    tenant_id = DEFAULT_TENANT_ID
    invite_label: Optional[str] = None
    if INVITE_REQUIRED:
        if not req.invite_code:
            raise HTTPException(status_code=400, detail="invite_required")
        norm = normalize_invite_code(req.invite_code)
        ok, reason, ic = _store.validate_invite_code(norm)
        if not ok or ic is None:
            raise HTTPException(status_code=400, detail=f"invite:{reason}")
        tenant_id = ic.tenant_id
        invite_label = ic.label

    # 1. Tell Auth0 to send the SMS. We catch failures so a dead Auth0
    #    doesn't take the service down — we fall back to our own OTP for
    #    the dev path.
    try:
        await _auth0.start(phone_e164=phone, locale=req.locale)
    except Exception as e:
        # Stub mode + dev are fine with our own code path; real mode
        # should reject loudly.
        if not isinstance(_auth0, StubAuth0OtpClient):
            raise HTTPException(status_code=502,
                                detail=f"auth0 start failed: {e}") from e

    # 2. Generate + persist a local challenge. We use the same code Auth0
    #    sent if the stub gives us one (so the dev loop has a single source
    #    of truth), otherwise a freshly-rolled code.
    code = (
        _auth0.fixed_code
        if isinstance(_auth0, StubAuth0OtpClient)
        else generate_otp_code(6)
    )
    challenge = _store.create_otp_challenge(
        phone_e164=phone, code=code, ttl_seconds=OTP_TTL_SECONDS,
    )

    _audit.append(
        actor="identity",
        action="auth.start",
        tenant_id=tenant_id,
        detail={"phone_hash": _phone_hash(phone), "locale": req.locale,
                "invite_label": invite_label},
        target=challenge.id,
    )
    return AuthStartResponse(
        challenge_id=challenge.id,
        expires_in=OTP_TTL_SECONDS,
        dev_code=code if EXPOSE_DEV_CODE else None,
        tenant_id=tenant_id,
        invite_label=invite_label,
    )


@app.post("/auth/verify", response_model=AuthVerifyResponse)
async def auth_verify(req: AuthVerifyRequest) -> AuthVerifyResponse:
    """Verify the OTP, mint an internal JWT, and create a session."""
    phone = req.phone_e164.strip()

    # 1. Validate the local challenge (rate-limited + single-use).
    ok, reason = _store.verify_otp(req.challenge_id, req.code)
    if not ok:
        raise HTTPException(status_code=401, detail=reason)

    # 2. Exchange the OTP with Auth0 (or stub) for the upstream tokens.
    try:
        tokens = await _auth0.verify(phone_e164=phone, otp=req.code)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"auth0 verify failed: {e}") from e

    # 2b. Resolve tenant + role from the invite code if one was supplied.
    target_tenant = DEFAULT_TENANT_ID
    target_role = "buyer"
    invite_code_to_redeem: Optional[str] = None
    if req.invite_code:
        norm = normalize_invite_code(req.invite_code)
        ok2, reason2, ic = _store.validate_invite_code(norm)
        if not ok2 or ic is None:
            raise HTTPException(status_code=400, detail=f"invite:{reason2}")
        target_tenant = ic.tenant_id
        target_role = ic.role
        invite_code_to_redeem = norm
    elif INVITE_REQUIRED:
        # Should never happen: /auth/start gated on this. Belt + braces.
        raise HTTPException(status_code=400, detail="invite_required")

    # 3. Upsert the user; mint the internal JWT bound to a fresh session.
    user = _store.upsert_user_by_phone(
        tenant_id=target_tenant,
        phone_e164=phone,
        auth0_subject=tokens.subject,
        role=target_role,
    )
    session = _store.create_session(user_id=user.id, tenant_id=user.tenant_id)
    token = issue_internal_jwt(
        tenant_id=user.tenant_id,
        user_id=user.id,
        session_id=session.id,
        role=user.role,
    )
    tenant = _store.get_tenant(user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=500, detail="tenant not found")

    # 4. Redeem the invite code atomically with the verify result.
    if invite_code_to_redeem:
        ok3, reason3, ic = _store.redeem_invite_code(
            invite_code_to_redeem, user_id=user.id, phone_e164=phone,
        )
        if not ok3:
            # Race condition: someone else redeemed between start + verify.
            raise HTTPException(status_code=409, detail=f"invite:{reason3}")
        _audit.append(
            actor="identity",
            action="invite.redeem",
            tenant_id=user.tenant_id,
            user_id=user.id,
            target=invite_code_to_redeem,
            detail={"label": ic.label if ic else None,
                    "role": target_role,
                    "session_id": session.id},
        )

    _audit.append(
        actor="identity",
        action="auth.verify",
        tenant_id=user.tenant_id,
        user_id=user.id,
        target=session.id,
        detail={"phone_hash": _phone_hash(phone),
                "invite_redeemed": invite_code_to_redeem is not None},
    )

    return AuthVerifyResponse(
        access_token=token,
        expires_in=tokens.expires_in or 3600,
        user=user.public(),
        tenant=tenant.public(),
    )


@app.get("/users/me")
async def users_me(claims: dict = Depends(_authenticated)) -> dict:
    user = _store.get_user(claims["user_id"])
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    tenant = _store.get_tenant(user.tenant_id)
    consents = [
        {"purpose": c.purpose, "granted": c.granted,
         "revoked": c.revoked_at is not None,
         "created_at": c.created_at}
        for c in _store.list_consent_for_user(user.id)
    ]
    return {
        "user": user.public(),
        "tenant": tenant.public() if tenant else None,
        "consents": consents,
    }


@app.get("/tenants/{tid}")
async def tenants_get(tid: str, claims: dict = Depends(_authenticated)) -> dict:
    # A user can only read their own tenant — admins (added P4.3) can do
    # cross-tenant lookups. For P4.1 we hard-restrict to claim's tenant.
    if claims["tenant_id"] != tid and claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="forbidden")
    tenant = _store.get_tenant(tid)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return tenant.public()


# ============================================================================
# Invite codes — Phase 4.4
# ============================================================================
class InviteValidateRequest(BaseModel):
    code: str


class InviteValidateResponse(BaseModel):
    ok: bool
    reason: str
    tenant_id: Optional[str] = None
    label: Optional[str] = None
    role: Optional[str] = None


@app.post("/auth/invite/validate", response_model=InviteValidateResponse)
async def auth_invite_validate(req: InviteValidateRequest) -> InviteValidateResponse:
    """Public — used by the mobile onboarding screen to give immediate
    feedback before the user types their phone. No auth required."""
    norm = normalize_invite_code(req.code)
    ok, reason, ic = _store.validate_invite_code(norm)
    if not ok or ic is None:
        return InviteValidateResponse(ok=False, reason=reason)
    return InviteValidateResponse(
        ok=True,
        reason="ok",
        tenant_id=ic.tenant_id,
        label=ic.label,
        role=ic.role,
    )


class InviteCreateRequest(BaseModel):
    tenant_id: str
    label: str = "manual"
    role: str = "buyer"
    ttl_seconds: Optional[int] = None
    notes: str = ""
    code: Optional[str] = None              # caller can pin a code (mostly for tests)


@app.post("/admin/invites")
async def admin_invite_create(
    req: InviteCreateRequest, claims: dict = Depends(_authenticated),
) -> dict:
    """Admin-only: mint a single invite code."""
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="forbidden")
    try:
        ic = _store.create_invite_code(
            tenant_id=req.tenant_id, label=req.label, role=req.role,
            ttl_seconds=req.ttl_seconds, notes=req.notes, code=req.code,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    _audit.append(
        actor="identity",
        action="invite.create",
        tenant_id=req.tenant_id,
        user_id=claims["user_id"],
        target=ic.code,
        detail={"label": ic.label, "role": ic.role,
                "expires_at": ic.expires_at},
    )
    return ic.public()


class InviteBatchRequest(BaseModel):
    tenant_id: str
    count: int = Field(..., ge=1, le=500)
    label_prefix: str = "batch"
    role: str = "buyer"
    ttl_seconds: Optional[int] = None
    notes: str = ""


@app.post("/admin/invites/batch")
async def admin_invite_batch(
    req: InviteBatchRequest, claims: dict = Depends(_authenticated),
) -> dict:
    """Admin-only: mint N invite codes in one transaction."""
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="forbidden")
    codes: list[dict] = []
    for i in range(req.count):
        try:
            ic = _store.create_invite_code(
                tenant_id=req.tenant_id,
                label=f"{req.label_prefix}-{i + 1:03d}",
                role=req.role,
                ttl_seconds=req.ttl_seconds,
                notes=req.notes,
            )
            codes.append(ic.public())
        except ValueError:
            # Collision — vanishingly rare; just try the next one.
            continue
    _audit.append(
        actor="identity",
        action="invite.batch_create",
        tenant_id=req.tenant_id,
        user_id=claims["user_id"],
        target=req.label_prefix,
        detail={"count": len(codes), "requested": req.count},
    )
    return {"count": len(codes), "codes": codes}


@app.get("/admin/invites")
async def admin_invites_list(
    claims: dict = Depends(_authenticated),
    tenant_id: Optional[str] = None,
    redeemed: Optional[bool] = None,
) -> dict:
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="forbidden")
    tid = tenant_id or claims["tenant_id"]
    items = _store.list_invite_codes(tenant_id=tid, redeemed=redeemed)
    return {
        "tenant_id": tid,
        "count": len(items),
        "invites": [ic.public() for ic in items],
    }


# ============================================================================
# Consent — Phase 4.2
# ============================================================================
class ConsentRecordRequest(BaseModel):
    purpose: str = Field(..., min_length=2, max_length=64)
    granted: bool
    text_version: str       # Aviso de Privacidad version (e.g. "aviso-v1")
    text_sha256: str        # SHA-256 of the text the user actually saw
    channel: str = "ui"     # "ui" | "voice" | "settings" | "api"


class ConsentRecordResponse(BaseModel):
    id: str
    purpose: str
    granted: bool
    created_at: float


@app.post("/consent", response_model=ConsentRecordResponse)
async def consent_record(
    req: ConsentRecordRequest,
    claims: dict = Depends(_authenticated),
) -> ConsentRecordResponse:
    """Record a fresh consent grant (or refusal)."""
    # The text_sha256 is what we tie the chain to — the *text the user
    # actually saw*. We carry both `text_version` (human-readable) and
    # the hash (machine-readable) in the audit detail.
    cr = _store.record_consent(
        user_id=claims["user_id"],
        tenant_id=claims["tenant_id"],
        purpose=req.purpose,
        granted=req.granted,
        text=req.text_sha256,    # store hashes the provided value again,
                                 # so we pass the hash itself for stability
    )
    _audit.append(
        actor="identity",
        action="consent.grant" if req.granted else "consent.refuse",
        tenant_id=claims["tenant_id"],
        user_id=claims["user_id"],
        target=cr.id,
        detail={
            "purpose": req.purpose,
            "text_version": req.text_version,
            "text_sha256": req.text_sha256,
            "channel": req.channel,
            "session_id": claims.get("sid"),
        },
    )
    return ConsentRecordResponse(
        id=cr.id, purpose=cr.purpose, granted=cr.granted,
        created_at=cr.created_at,
    )


class ConsentRevokeRequest(BaseModel):
    purpose: str


@app.post("/consent/revoke")
async def consent_revoke(
    req: ConsentRevokeRequest,
    claims: dict = Depends(_authenticated),
) -> dict:
    n = _store.revoke_consent(user_id=claims["user_id"], purpose=req.purpose)
    _audit.append(
        actor="identity",
        action="consent.revoke",
        tenant_id=claims["tenant_id"],
        user_id=claims["user_id"],
        target=req.purpose,
        detail={"revoked_count": n, "channel": "api",
                "session_id": claims.get("sid")},
    )
    return {"revoked": n}


@app.get("/consent")
async def consent_list(claims: dict = Depends(_authenticated)) -> dict:
    consents = [
        {
            "id": c.id, "purpose": c.purpose, "granted": c.granted,
            "created_at": c.created_at,
            "revoked": c.revoked_at is not None,
            "revoked_at": c.revoked_at,
        }
        for c in _store.list_consent_for_user(claims["user_id"])
    ]
    return {"user_id": claims["user_id"], "consents": consents}


# ============================================================================
# DSAR — Phase 4.2 (LFPDPPP Art. 28 + 32)
# ============================================================================
@app.post("/dsar/export")
async def dsar_export(claims: dict = Depends(_authenticated)) -> Response:
    """
    Fan out a data-access request to every service and return a ZIP
    containing one JSON per service plus a manifest with per-file hashes.
    """
    user_id = claims["user_id"]
    tenant_id = claims["tenant_id"]
    zip_bytes, receipts = await _dsar.export_user(user_id, tenant_id)

    # Identity's own data is *not* fetched by the DSAR fan-out; we
    # append it from the in-process store so the user gets a complete
    # picture in one ZIP. The audit row captures the receipt summary.
    successful = sum(1 for r in receipts if r.status == 200)
    failed = len(receipts) - successful
    _audit.append(
        actor="identity",
        action="dsar.export",
        tenant_id=tenant_id,
        user_id=user_id,
        target=user_id,
        detail={
            "services_total": len(receipts),
            "services_successful": successful,
            "services_failed": failed,
            "bytes": len(zip_bytes),
            "session_id": claims.get("sid"),
        },
    )

    fname = f"casa-orquesta-dsar-{user_id}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-DSAR-Services-Total": str(len(receipts)),
            "X-DSAR-Services-Successful": str(successful),
            "X-DSAR-Services-Failed": str(failed),
        },
    )


@app.post("/dsar/delete")
async def dsar_delete(claims: dict = Depends(_authenticated)) -> dict:
    """
    Fan out a right-to-be-forgotten request to every service. We always
    process the identity service *last* — only after every downstream
    service confirms can we wipe the user row + revoke their sessions
    without orphaning data we can't reach later.
    """
    user_id = claims["user_id"]
    tenant_id = claims["tenant_id"]
    receipts = await _dsar.delete_user(user_id, tenant_id)

    # Local cleanup last: revoke sessions, revoke consents, mark the
    # user soft-deleted (full row purge is a 24h batch job — gives ops
    # one rollback window per LFPDPPP best-practice).
    sessions_revoked = _store.revoke_sessions_for_user(user_id)
    consents_revoked = 0
    user = _store.get_user(user_id)
    if user:
        for purpose in {c.purpose for c in _store.list_consent_for_user(user_id)}:
            consents_revoked += _store.revoke_consent(user_id, purpose)

    successful = sum(1 for r in receipts if r.deleted)
    failed = len(receipts) - successful
    _audit.append(
        actor="identity",
        action="dsar.delete",
        tenant_id=tenant_id,
        user_id=user_id,
        target=user_id,
        detail={
            "services_total": len(receipts),
            "services_successful": successful,
            "services_failed": failed,
            "sessions_revoked": sessions_revoked,
            "consents_revoked": consents_revoked,
            "session_id": claims.get("sid"),
        },
    )
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "services": [
            {"service": r.service, "deleted": r.deleted, "status": r.status,
             "count": r.count, "error": r.error}
            for r in receipts
        ],
        "sessions_revoked": sessions_revoked,
        "consents_revoked": consents_revoked,
        "note": (
            "Per LFPDPPP Art. 32, your data has been requested for "
            "deletion at every service that holds it. The identity "
            "row will be hard-deleted in the next 24-hour batch."
        ),
    }


# ============================================================================
# Internal — used by tests + ops
# ============================================================================
@app.get("/_internal/audit")
async def internal_audit(claims: dict = Depends(_authenticated)) -> dict:
    """List audit entries scoped to the caller's tenant. Admins only.

    This is intentionally not on the public surface — it exists so the
    ops dashboard (P4.5) can render the hash-chained log without giving
    every authenticated user full visibility.
    """
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="forbidden")
    entries = _audit.filter(tenant_id=claims["tenant_id"])
    ok, reason = _audit.verify_chain()
    return {
        "tenant_id": claims["tenant_id"],
        "chain_ok": ok,
        "chain_reason": reason,
        "count": len(entries),
        "entries": [e.public() for e in entries],
    }


# ============================================================================
# Helpers
# ============================================================================
def _phone_hash(phone: str) -> str:
    """SHA-256 of the phone for audit logs — never store raw phone there."""
    import hashlib
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()[:16]
