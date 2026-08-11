"""
Audit log hook — Phase 1.7.

Per CLAUDE.md non-negotiable #6: append-only, tamper-evident, hash-chained.
Each entry includes the SHA-256 of the previous entry, so any mutation
breaks the chain at and after the modified entry.

Backends:
  AUDIT_BACKEND=local  → JSONL file at /app/log/audit.jsonl
  AUDIT_BACKEND=s3     → S3/Tigris bucket with object lock (WORM). Set:
                         S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY.

The hook never raises — a failed write is logged but doesn't fail the agent.
The chain is per-process; concurrent writers MUST use the S3 backend with
ETag-conditional puts (P4.4 — LFPIORPI monthly report wiring).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

AUDIT_BACKEND = os.getenv("AUDIT_BACKEND", "local").lower()
AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", "/app/log/audit.jsonl")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_PREFIX = os.getenv("AUDIT_S3_PREFIX", "audit/")

_GENESIS_HASH = "0" * 64
_chain_lock = threading.Lock()
_prev_hash: str = _GENESIS_HASH

# /app/log is the container path; on a host dev run it isn't writable,
# so fall back to a log dir inside the service tree instead of letting
# the whole hooks package fail to import.
try:
    os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
except OSError:
    AUDIT_LOG_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "log", "audit.jsonl",
    )
    os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
    print(f"[hooks.audit_log] default path unwritable; using {AUDIT_LOG_PATH}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seal(entry: dict) -> dict:
    """Sign an entry with prev-hash + own SHA-256, in-place. Thread-safe."""
    global _prev_hash
    with _chain_lock:
        entry["prev_hash"] = _prev_hash
        body = json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str)
        own = hashlib.sha256(body.encode("utf-8")).hexdigest()
        entry["hash"] = own
        _prev_hash = own
    return entry


def _write_local(entry: dict) -> None:
    try:
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        print(f"[hooks.audit_log] local write failed: {e}")


def _write_s3(entry: dict) -> None:
    try:
        import boto3  # type: ignore
        s3 = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
        )
        key = f"{S3_PREFIX}{datetime.now(timezone.utc).strftime('%Y/%m/%d')}/{entry['id']}.json"
        s3.put_object(
            Bucket=S3_BUCKET, Key=key,
            Body=json.dumps(entry, ensure_ascii=False, default=str).encode("utf-8"),
            ContentType="application/json",
            ObjectLockMode="COMPLIANCE",
            # 7-year retention — LFPIORPI requirement.
            # Real prod: set ObjectLockRetainUntilDate via a per-bucket policy.
        )
    except Exception as e:
        # WORM put failures are LFPIORPI-relevant; surface but never raise.
        print(f"[hooks.audit_log] S3 put failed: {e}")
        # Fall back to local so the entry isn't lost.
        _write_local(entry)


def record(actor: str, action: str, resource: str,
           detail: Optional[dict] = None,
           tenant_id: Optional[str] = None,
           run_id: Optional[str] = None) -> dict:
    """
    Append a single entry to the audit chain. Returns the sealed entry.

    Schema (every entry):
      id, ts, actor, action, resource, detail, tenant_id, run_id,
      prev_hash, hash
    """
    entry = {
        "id": f"A-{uuid.uuid4().hex[:12].upper()}",
        "ts": _now_iso(),
        "actor": actor,
        "action": action,
        "resource": resource,
        "detail": detail or {},
        "tenant_id": tenant_id,
        "run_id": run_id,
    }
    entry = _seal(entry)
    if AUDIT_BACKEND == "s3" and S3_BUCKET:
        _write_s3(entry)
    else:
        _write_local(entry)
    return entry


def chain_head() -> str:
    """Current chain head hash. Useful for snapshot verification."""
    return _prev_hash


def verify_local_chain(path: Optional[str] = None) -> dict:
    """Walk a local JSONL log and verify every prev_hash → hash link.
    Returns {ok, count, broken_at}."""
    path = path or AUDIT_LOG_PATH
    if not os.path.exists(path):
        return {"ok": True, "count": 0, "broken_at": None}
    expected = _GENESIS_HASH
    count = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("prev_hash") != expected:
                    return {"ok": False, "count": count, "broken_at": entry.get("id")}
                # Recompute own hash to check tamper-evidence.
                copy = {k: v for k, v in entry.items() if k != "hash"}
                body = json.dumps(copy, sort_keys=True, ensure_ascii=False, default=str)
                own = hashlib.sha256(body.encode("utf-8")).hexdigest()
                if own != entry.get("hash"):
                    return {"ok": False, "count": count, "broken_at": entry.get("id")}
                expected = entry["hash"]
                count += 1
        return {"ok": True, "count": count, "broken_at": None}
    except Exception as e:
        return {"ok": False, "count": count, "broken_at": str(e)}


def reset_for_tests() -> None:
    """Test helper — reset the in-process chain head."""
    global _prev_hash
    with _chain_lock:
        _prev_hash = _GENESIS_HASH
