"""
Shared audit log — Phase 4.2.

Append-only, hash-chained audit trail. Every entry carries the SHA-256
of its predecessor's content_hash + its own canonical JSON payload, so
any tamper or reordering is detectable by recomputing the chain.

Shape matches `services/identity/migrations/0001_init.sql > audit_log`:

    AuditEntry(
        id          : int     # autoincrement (assigned by store)
        tenant_id   : str|None
        user_id     : str|None
        actor       : str     # 'identity' | 'orchestrator' | …
        action      : str     # 'auth.start' | 'consent.grant' | 'dsar.export' | …
        target      : str|None
        detail      : dict    # JSON-serializable
        ts          : float   # epoch seconds
        prev_hash   : str|None
        content_hash: str
    )

The in-memory `AuditLog` is what the standalone test suite uses. The
Postgres adapter (P4.5) wraps the same `append()` + `verify_chain()`
contract over the `audit_log` table.

Why a separate module instead of putting this in `services/identity`?
Because every service writes audit entries (`orchestrator/hooks/audit.py`
predates this file). Centralising the hash-chain logic means there's
one place that decides what "canonical JSON" looks like — otherwise
the chain breaks when two services use different `separators=` for
their `json.dumps`.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
@dataclass
class AuditEntry:
    actor: str
    action: str
    detail: dict = field(default_factory=dict)
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    target: Optional[str] = None
    ts: float = 0.0
    id: int = 0
    prev_hash: Optional[str] = None
    content_hash: str = ""

    def public(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Canonical hashing
# ---------------------------------------------------------------------------
def _canonical(entry: AuditEntry) -> bytes:
    """Stable JSON of the fields that participate in the chain.

    Excludes `id` and `content_hash` (output of the hash). Includes
    `prev_hash` so re-linking changes the digest.
    """
    payload = {
        "actor": entry.actor,
        "action": entry.action,
        "detail": entry.detail,
        "tenant_id": entry.tenant_id,
        "user_id": entry.user_id,
        "target": entry.target,
        "ts": entry.ts,
        "prev_hash": entry.prev_hash,
    }
    return json.dumps(
        payload, separators=(",", ":"), sort_keys=True, default=str,
    ).encode("utf-8")


def compute_content_hash(entry: AuditEntry) -> str:
    return hashlib.sha256(_canonical(entry)).hexdigest()


# ---------------------------------------------------------------------------
# In-memory log
# ---------------------------------------------------------------------------
class AuditLog:
    """Append-only hash-chained audit log. Thread-safety is the caller's
    responsibility (we wrap a list, no locks)."""

    def __init__(self, *, now_fn: Callable[[], float] = time.time):
        self._entries: list[AuditEntry] = []
        self._now = now_fn

    # ----- append -----
    def append(
        self,
        *,
        actor: str,
        action: str,
        detail: Optional[dict] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        target: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            actor=actor,
            action=action,
            detail=detail or {},
            tenant_id=tenant_id,
            user_id=user_id,
            target=target,
            ts=ts if ts is not None else self._now(),
            id=len(self._entries) + 1,
            prev_hash=self._entries[-1].content_hash if self._entries else None,
        )
        entry.content_hash = compute_content_hash(entry)
        self._entries.append(entry)
        return entry

    # ----- reads -----
    def all(self) -> list[AuditEntry]:
        return list(self._entries)

    def filter(
        self,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        action_prefix: Optional[str] = None,
    ) -> list[AuditEntry]:
        out: list[AuditEntry] = []
        for e in self._entries:
            if tenant_id is not None and e.tenant_id != tenant_id:
                continue
            if user_id is not None and e.user_id != user_id:
                continue
            if action_prefix is not None and not e.action.startswith(action_prefix):
                continue
            out.append(e)
        return out

    def __len__(self) -> int:
        return len(self._entries)

    # ----- integrity -----
    def verify_chain(self) -> tuple[bool, str]:
        """
        Return (ok, reason).

        Recomputes each entry's content_hash and re-checks that prev_hash
        points back at the previous entry's content_hash. Returns the
        first broken link (or 'ok' if the whole chain validates).
        """
        prev: Optional[str] = None
        for e in self._entries:
            if e.prev_hash != prev:
                return False, f"entry id={e.id}: prev_hash mismatch"
            expected = compute_content_hash(e)
            if e.content_hash != expected:
                return False, f"entry id={e.id}: content_hash mismatch"
            prev = e.content_hash
        return True, "ok"
