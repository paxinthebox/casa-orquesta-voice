"""
DSAR fan-out client + ZIP builder — Phase 4.2.

Right-of-access (LFPDPPP art. 28) and right-to-be-forgotten (LFPDPPP art. 32):

  * Export: identity fans out a GET to every registered service's
    `/dsar/user/{user_id}` endpoint and zips the responses + a signed
    manifest with per-file SHA-256 hashes.

  * Delete: identity fans out a DELETE to every registered service's
    `/dsar/user/{user_id}` endpoint. Each response is a deletion
    receipt with `{service, user_id, deleted, count, ts}`. The identity
    service is *last* — only after every other service confirmed do we
    wipe the user row.

Both operations write an audit_log entry per service (success or
failure). Failures don't abort the fan-out; the receipt records them
and the user gets a follow-up email (P4.5 ops).

Both operations use an injectable `_HTTPClient` Protocol so unit tests
drive deterministic mocks without httpx.

The service registry is read from `IDENTITY_DSAR_SERVICES` env (default
includes every Phase 1–3 service); each entry is `{name, base_url}`.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
import zipfile
from dataclasses import asdict, dataclass
from typing import Any, Callable, Optional, Protocol


# ---------------------------------------------------------------------------
# Service registry
# ---------------------------------------------------------------------------
@dataclass
class ServiceEntry:
    name: str
    base_url: str


def default_registry() -> list[ServiceEntry]:
    """
    Default Phase 1–3 registry. Override via IDENTITY_DSAR_SERVICES
    (JSON: [{"name":"listings","base_url":"http://listings:8000"}, …]).

    The identity service is intentionally NOT in this list — it's the
    coordinator and gets handled last by the route handler.
    """
    raw = os.getenv("IDENTITY_DSAR_SERVICES")
    if raw:
        try:
            arr = json.loads(raw)
            return [ServiceEntry(name=e["name"], base_url=e["base_url"])
                    for e in arr]
        except Exception:
            pass
    base = os.getenv("INTERNAL_BASE", "http://localhost")
    return [
        ServiceEntry("orchestrator",  f"{base}:8000"),
        ServiceEntry("voice-gateway", f"{base}:8001"),
        ServiceEntry("listings",      f"{base}:8003"),
        ServiceEntry("scheduling",    f"{base}:8004"),
        ServiceEntry("documents",     f"{base}:8005"),
        ServiceEntry("payments",      f"{base}:8006"),
        ServiceEntry("comms",         f"{base}:8007"),
    ]


# ---------------------------------------------------------------------------
# HTTP surface (injectable)
# ---------------------------------------------------------------------------
class _HTTPResponse(Protocol):
    status_code: int
    def json(self) -> Any: ...
    @property
    def content(self) -> bytes: ...


class _HTTPClient(Protocol):
    async def get(self, url: str, *, headers: dict, timeout: float) -> _HTTPResponse: ...
    async def delete(self, url: str, *, headers: dict, timeout: float) -> _HTTPResponse: ...
    async def aclose(self) -> None: ...


HTTPFactory = Callable[[], _HTTPClient]


def _default_http_factory() -> _HTTPClient:
    import httpx  # type: ignore

    class _Wrapper:
        def __init__(self):
            self._client = httpx.AsyncClient()
        async def get(self, url, *, headers, timeout):
            return await self._client.get(url, headers=headers, timeout=timeout)
        async def delete(self, url, *, headers, timeout):
            return await self._client.delete(url, headers=headers, timeout=timeout)
        async def aclose(self):
            await self._client.aclose()

    return _Wrapper()


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------
@dataclass
class ExportReceipt:
    service: str
    status: int
    bytes_written: int
    sha256: str
    error: Optional[str] = None


@dataclass
class DeleteReceipt:
    service: str
    status: int
    deleted: bool
    count: int = 0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# DsarClient
# ---------------------------------------------------------------------------
class DsarClient:
    def __init__(
        self,
        *,
        registry: Optional[list[ServiceEntry]] = None,
        http_factory: HTTPFactory = _default_http_factory,
        timeout: float = 30.0,
        internal_token: Optional[str] = None,
    ):
        self.registry = registry if registry is not None else default_registry()
        self._http_factory = http_factory
        self._timeout = timeout
        self._internal_token = internal_token or os.getenv("IDENTITY_INTERNAL_TOKEN", "")

    # ----- Export -----
    async def export_user(self, user_id: str, tenant_id: str) -> tuple[bytes, list[ExportReceipt]]:
        """Fan-out GET → ZIP of `{service}.json` files + `manifest.json`."""
        http = self._http_factory()
        receipts: list[ExportReceipt] = []
        buf = io.BytesIO()
        try:
            with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for svc in self.registry:
                    receipt, payload = await self._fetch_one(http, svc, user_id, tenant_id)
                    receipts.append(receipt)
                    zf.writestr(f"{svc.name}.json", payload)
                # Manifest last so it can reference every payload's hash.
                manifest = self._build_manifest(user_id, tenant_id, receipts)
                zf.writestr("manifest.json",
                            json.dumps(manifest, indent=2, sort_keys=True, default=str))
        finally:
            try: await http.aclose()
            except Exception: pass
        return buf.getvalue(), receipts

    async def _fetch_one(
        self,
        http: _HTTPClient,
        svc: ServiceEntry,
        user_id: str,
        tenant_id: str,
    ) -> tuple[ExportReceipt, str]:
        url = f"{svc.base_url.rstrip('/')}/dsar/user/{user_id}"
        headers = {
            "Accept": "application/json",
            "X-Tenant-Id": tenant_id,
        }
        if self._internal_token:
            headers["Authorization"] = f"Bearer {self._internal_token}"
        try:
            resp = await http.get(url, headers=headers, timeout=self._timeout)
            status = int(getattr(resp, "status_code", 0))
            if status != 200:
                payload = json.dumps(
                    {"service": svc.name, "error": f"HTTP {status}",
                     "user_id": user_id, "tenant_id": tenant_id},
                    indent=2)
                receipt = ExportReceipt(
                    service=svc.name,
                    status=status,
                    bytes_written=len(payload),
                    sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                    error=f"HTTP {status}",
                )
                return receipt, payload
            body = resp.json()
            payload = json.dumps(body, indent=2, sort_keys=True, default=str)
            receipt = ExportReceipt(
                service=svc.name,
                status=status,
                bytes_written=len(payload),
                sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            )
            return receipt, payload
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            payload = json.dumps(
                {"service": svc.name, "error": err,
                 "user_id": user_id, "tenant_id": tenant_id},
                indent=2)
            receipt = ExportReceipt(
                service=svc.name,
                status=0,
                bytes_written=len(payload),
                sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                error=err,
            )
            return receipt, payload

    def _build_manifest(
        self,
        user_id: str,
        tenant_id: str,
        receipts: list[ExportReceipt],
    ) -> dict:
        return {
            "schema_version": 1,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "exported_at": time.time(),
            "services": [asdict(r) for r in receipts],
            "summary": {
                "total_services": len(receipts),
                "successful": sum(1 for r in receipts if r.status == 200),
                "failed": sum(1 for r in receipts if r.status != 200),
            },
            "note": (
                "Generated for compliance with LFPDPPP Art. 28 — derecho de "
                "acceso. Each file contains the data this service holds about "
                "the user. The identity service's own consent ledger is "
                "included separately in `identity.json` (assembled by the "
                "identity route handler, not the DSAR fan-out client)."
            ),
        }

    # ----- Delete -----
    async def delete_user(self, user_id: str, tenant_id: str) -> list[DeleteReceipt]:
        """Fan-out DELETE. Returns a per-service receipt list."""
        http = self._http_factory()
        receipts: list[DeleteReceipt] = []
        try:
            for svc in self.registry:
                receipts.append(await self._delete_one(http, svc, user_id, tenant_id))
        finally:
            try: await http.aclose()
            except Exception: pass
        return receipts

    async def _delete_one(
        self,
        http: _HTTPClient,
        svc: ServiceEntry,
        user_id: str,
        tenant_id: str,
    ) -> DeleteReceipt:
        url = f"{svc.base_url.rstrip('/')}/dsar/user/{user_id}"
        headers = {
            "Accept": "application/json",
            "X-Tenant-Id": tenant_id,
        }
        if self._internal_token:
            headers["Authorization"] = f"Bearer {self._internal_token}"
        try:
            resp = await http.delete(url, headers=headers, timeout=self._timeout)
            status = int(getattr(resp, "status_code", 0))
            if status not in (200, 204):
                return DeleteReceipt(
                    service=svc.name, status=status, deleted=False,
                    error=f"HTTP {status}",
                )
            body = resp.json() if status == 200 else {"deleted": True, "count": 0}
            return DeleteReceipt(
                service=svc.name,
                status=status,
                deleted=bool(body.get("deleted", True)),
                count=int(body.get("count", 0) or 0),
            )
        except Exception as e:
            return DeleteReceipt(
                service=svc.name, status=0, deleted=False,
                error=f"{type(e).__name__}: {e}",
            )
