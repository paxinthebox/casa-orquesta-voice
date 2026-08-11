"""
Identity store — Phase 4.1.

Backing store for users, tenants, consent records, and active sessions.
Production uses Postgres (see `migrations/0001_init.sql`); tests and
sandbox use the in-memory fallback so the unit suite stays hermetic.

The store deliberately accepts callables for `now_fn` and `id_fn` so the
tests can pin time + ids and assert deterministic records.
"""
from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Optional

# Tenant + user ids stay opaque outside the store; we type them as `str`
# for everything else in the codebase.
TenantId = str
UserId = str


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@dataclass
class Tenant:
    id: TenantId
    name: str
    country: str = "MX"
    created_at: float = 0.0
    plan: str = "pilot"

    def public(self) -> dict:
        return asdict(self)


@dataclass
class User:
    id: UserId
    tenant_id: TenantId
    phone_e164: str
    role: str = "buyer"                 # buyer | broker | admin
    display_name: Optional[str] = None
    locale: str = "es-MX"
    created_at: float = 0.0
    last_login_at: float = 0.0
    auth0_subject: Optional[str] = None

    def public(self) -> dict:
        d = asdict(self)
        # Never expose the auth0 subject to non-admin consumers.
        d.pop("auth0_subject", None)
        return d


@dataclass
class ConsentRecord:
    id: str
    user_id: UserId
    tenant_id: TenantId
    purpose: str                         # "lfpdppp" | "mic" | "transcripts" | "marketing"
    granted: bool
    text_hash: str
    created_at: float
    revoked_at: Optional[float] = None


@dataclass
class Session:
    id: str                              # opaque session id (also embedded in JWT jti)
    user_id: UserId
    tenant_id: TenantId
    created_at: float
    last_seen_at: float
    revoked_at: Optional[float] = None
    user_agent: Optional[str] = None
    device_id: Optional[str] = None


@dataclass
class OtpChallenge:
    id: str
    phone_e164: str
    code_hash: str                       # SHA-256 of the code; never store raw
    expires_at: float
    attempts: int = 0
    consumed_at: Optional[float] = None


@dataclass
class InviteCode:
    """Tenant-scoped invite token for the closed beta.

    Lifecycle:
        generate → distribute (out-of-band) → user enters → validate
        → redeem on /auth/verify (binds to phone + user)
    """
    code: str                            # human-typed, uppercase, 4-4 format
    tenant_id: TenantId
    label: str                           # ops-friendly: "may-batch-1", "broker-1"
    role: str = "buyer"                  # role granted on redemption
    created_at: float = 0.0
    expires_at: Optional[float] = None
    redeemed_at: Optional[float] = None
    redeemed_by_user_id: Optional[UserId] = None
    redeemed_by_phone: Optional[str] = None
    notes: str = ""

    def public(self) -> dict:
        return {
            "code": self.code,
            "tenant_id": self.tenant_id,
            "label": self.label,
            "role": self.role,
            "expires_at": self.expires_at,
            "redeemed": self.redeemed_at is not None,
            "redeemed_at": self.redeemed_at,
        }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
class IdentityStore:
    """In-memory store. The Postgres adapter (P4.5) wraps this surface."""

    def __init__(
        self,
        *,
        now_fn: Callable[[], float] = time.time,
        id_fn: Callable[[], str] = lambda: uuid.uuid4().hex,
    ):
        self._now = now_fn
        self._id = id_fn

        self._tenants: dict[TenantId, Tenant] = {}
        self._users: dict[UserId, User] = {}
        self._users_by_phone: dict[str, UserId] = {}
        self._consent: list[ConsentRecord] = []
        self._sessions: dict[str, Session] = {}
        self._otp_challenges: dict[str, OtpChallenge] = {}
        self._invites: dict[str, InviteCode] = {}

        # Seed the pilot tenant so dev builds work out of the box.
        self.create_tenant(id="tnt_pilot_mx", name="Casa·Orquesta Pilot",
                           country="MX", plan="pilot")

    # ----- Tenants -----
    def create_tenant(self, *, id: Optional[str] = None, name: str,
                      country: str = "MX", plan: str = "pilot") -> Tenant:
        tid = id or f"tnt_{self._id()[:10]}"
        t = Tenant(id=tid, name=name, country=country, plan=plan,
                   created_at=self._now())
        self._tenants[tid] = t
        return t

    def get_tenant(self, tid: TenantId) -> Optional[Tenant]:
        return self._tenants.get(tid)

    # ----- Users -----
    def upsert_user_by_phone(
        self,
        *,
        tenant_id: TenantId,
        phone_e164: str,
        auth0_subject: Optional[str] = None,
        role: str = "buyer",
        locale: str = "es-MX",
    ) -> User:
        existing_id = self._users_by_phone.get(phone_e164)
        if existing_id:
            u = self._users[existing_id]
            u.last_login_at = self._now()
            if auth0_subject and not u.auth0_subject:
                u.auth0_subject = auth0_subject
            return u
        uid = f"u_{self._id()[:10]}"
        u = User(
            id=uid,
            tenant_id=tenant_id,
            phone_e164=phone_e164,
            role=role,
            locale=locale,
            created_at=self._now(),
            last_login_at=self._now(),
            auth0_subject=auth0_subject,
        )
        self._users[uid] = u
        self._users_by_phone[phone_e164] = uid
        return u

    def get_user(self, uid: UserId) -> Optional[User]:
        return self._users.get(uid)

    def get_user_by_phone(self, phone_e164: str) -> Optional[User]:
        uid = self._users_by_phone.get(phone_e164)
        return self._users.get(uid) if uid else None

    # ----- Consent -----
    def record_consent(self, *, user_id: UserId, tenant_id: TenantId,
                       purpose: str, granted: bool,
                       text: str) -> ConsentRecord:
        cr = ConsentRecord(
            id=f"cns_{self._id()[:10]}",
            user_id=user_id,
            tenant_id=tenant_id,
            purpose=purpose,
            granted=granted,
            text_hash=_sha256(text.encode("utf-8")),
            created_at=self._now(),
        )
        self._consent.append(cr)
        return cr

    def revoke_consent(self, user_id: UserId, purpose: str) -> int:
        count = 0
        for cr in self._consent:
            if (cr.user_id == user_id
                    and cr.purpose == purpose
                    and cr.revoked_at is None):
                cr.revoked_at = self._now()
                count += 1
        return count

    def has_active_consent(self, user_id: UserId, purpose: str) -> bool:
        for cr in self._consent:
            if (cr.user_id == user_id
                    and cr.purpose == purpose
                    and cr.granted
                    and cr.revoked_at is None):
                return True
        return False

    def list_consent_for_user(self, user_id: UserId) -> Iterable[ConsentRecord]:
        return [cr for cr in self._consent if cr.user_id == user_id]

    # ----- Sessions -----
    def create_session(self, *, user_id: UserId, tenant_id: TenantId,
                       user_agent: Optional[str] = None,
                       device_id: Optional[str] = None) -> Session:
        sid = f"sess_{self._id()[:14]}"
        s = Session(
            id=sid, user_id=user_id, tenant_id=tenant_id,
            created_at=self._now(), last_seen_at=self._now(),
            user_agent=user_agent, device_id=device_id,
        )
        self._sessions[sid] = s
        return s

    def touch_session(self, sid: str) -> Optional[Session]:
        s = self._sessions.get(sid)
        if s is None or s.revoked_at is not None:
            return None
        s.last_seen_at = self._now()
        return s

    def revoke_session(self, sid: str) -> bool:
        s = self._sessions.get(sid)
        if s is None:
            return False
        s.revoked_at = self._now()
        return True

    def revoke_sessions_for_user(self, user_id: UserId) -> int:
        count = 0
        for s in self._sessions.values():
            if s.user_id == user_id and s.revoked_at is None:
                s.revoked_at = self._now()
                count += 1
        return count

    # ----- OTP challenges -----
    def create_otp_challenge(self, *, phone_e164: str, code: str,
                             ttl_seconds: int = 300) -> OtpChallenge:
        ch = OtpChallenge(
            id=f"otp_{self._id()[:10]}",
            phone_e164=phone_e164,
            code_hash=_sha256(code.encode("utf-8")),
            expires_at=self._now() + ttl_seconds,
        )
        self._otp_challenges[ch.id] = ch
        return ch

    # ----- Invite codes -----
    def create_invite_code(
        self,
        *,
        tenant_id: TenantId,
        label: str,
        role: str = "buyer",
        ttl_seconds: Optional[int] = None,
        notes: str = "",
        code: Optional[str] = None,
    ) -> InviteCode:
        c = (code or generate_invite_code()).upper()
        if c in self._invites:
            raise ValueError(f"invite code collision: {c}")
        ic = InviteCode(
            code=c,
            tenant_id=tenant_id,
            label=label,
            role=role,
            created_at=self._now(),
            expires_at=(self._now() + ttl_seconds) if ttl_seconds else None,
            notes=notes,
        )
        self._invites[c] = ic
        return ic

    def get_invite_code(self, code: str) -> Optional[InviteCode]:
        return self._invites.get(code.strip().upper())

    def list_invite_codes(self, *, tenant_id: Optional[TenantId] = None,
                          redeemed: Optional[bool] = None) -> list[InviteCode]:
        out: list[InviteCode] = []
        for ic in self._invites.values():
            if tenant_id and ic.tenant_id != tenant_id:
                continue
            if redeemed is not None:
                if (ic.redeemed_at is not None) != redeemed:
                    continue
            out.append(ic)
        return out

    def validate_invite_code(self, code: str) -> tuple[bool, str, Optional[InviteCode]]:
        """Returns (ok, reason, ic). Doesn't redeem — see redeem_invite_code()."""
        c = code.strip().upper()
        ic = self._invites.get(c)
        if ic is None:
            return False, "unknown_code", None
        if ic.redeemed_at is not None:
            return False, "already_redeemed", ic
        if ic.expires_at is not None and self._now() > ic.expires_at:
            return False, "expired", ic
        return True, "ok", ic

    def redeem_invite_code(
        self, code: str, *, user_id: UserId, phone_e164: str,
    ) -> tuple[bool, str, Optional[InviteCode]]:
        ok, reason, ic = self.validate_invite_code(code)
        if not ok or ic is None:
            return ok, reason, ic
        ic.redeemed_at = self._now()
        ic.redeemed_by_user_id = user_id
        ic.redeemed_by_phone = phone_e164
        return True, "ok", ic

    def verify_otp(self, challenge_id: str, code: str) -> tuple[bool, str]:
        """Return (ok, reason). Side-effect: increments attempts; marks consumed on success."""
        ch = self._otp_challenges.get(challenge_id)
        if ch is None:
            return False, "unknown_challenge"
        if ch.consumed_at is not None:
            return False, "already_used"
        if self._now() > ch.expires_at:
            return False, "expired"
        ch.attempts += 1
        if ch.attempts > 5:
            return False, "too_many_attempts"
        if _sha256(code.encode("utf-8")) != ch.code_hash:
            return False, "bad_code"
        ch.consumed_at = self._now()
        return True, "ok"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def generate_otp_code(length: int = 6) -> str:
    """Cryptographically random numeric OTP."""
    n = 0
    while True:
        n = secrets.randbelow(10 ** length)
        # Avoid leading-zero codes that look ambiguous to users
        # (e.g., 000123 → "123" autofill). Re-roll if leading zero.
        if n >= 10 ** (length - 1):
            break
    return str(n)


# Invite-code alphabet — Crockford-base32-style, minus ambiguous glyphs
# (0/O, 1/I/L, U). Yields ~26-bit entropy per 4-char block.
_INVITE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_invite_code() -> str:
    """8-char invite code, formatted as `XXXX-XXXX`."""
    a = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(4))
    b = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(4))
    return f"{a}-{b}"


def normalize_invite_code(raw: str) -> str:
    """Strip whitespace + dashes, uppercase, re-insert the dash. Forgiving
    of common typing variants (lowercase, missing/extra dashes, etc.)."""
    s = (raw or "").strip().upper().replace(" ", "").replace("-", "")
    if len(s) != 8:
        return s
    return f"{s[:4]}-{s[4:]}"
