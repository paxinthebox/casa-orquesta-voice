"""EasyBroker API client — Mexico's MLS/CRM used by independent agents."""
from __future__ import annotations

import os
from typing import Any, AsyncIterator

import httpx

from .normalize import (
    easybroker_agent,
    easybroker_collaboration,
    easybroker_listing,
    easybroker_user,
)

API_BASE = os.getenv("EASYBROKER_API_BASE", "https://api.easybroker.com/v1")
PUBLISHED_ONLY = os.getenv("EASYBROKER_PUBLISHED_ONLY", "0").strip() in ("1", "true", "yes")


class EasyBrokerError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class EasyBrokerClient:
    def __init__(self, api_key: str, *, country_code: str = "MX") -> None:
        self._api_key = api_key
        self._country = country_code
        self._headers = {
            "X-Authorization": api_key,
            "Country-Code": country_code,
            "accept": "application/json",
        }

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{API_BASE.rstrip('/')}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers, params=params or {})
        if resp.status_code == 401:
            raise EasyBrokerError(
                "API key inválida — revisa EASYBROKER_API_KEY en .env",
                status_code=401,
            )
        if resp.status_code == 403:
            raise EasyBrokerError(
                f"Acceso denegado a {path} (¿plan MLS requerido?)",
                status_code=403,
            )
        if resp.status_code >= 400:
            raise EasyBrokerError(
                f"EasyBroker {path} → HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )
        data = resp.json()
        return data if isinstance(data, dict) else {"content": data}

    async def _paginate(
        self,
        path: str,
        *,
        extra_params: dict[str, Any] | None = None,
        limit: int = 50,
        max_pages: int = 20,
    ) -> AsyncIterator[dict[str, Any]]:
        page = 1
        while page <= max_pages:
            params: dict[str, Any] = {"page": page, "limit": min(limit, 50)}
            if extra_params:
                params.update(extra_params)
            payload = await self._get(path, params)
            rows = (
                payload.get("content")
                or payload.get("properties")
                or payload.get("pagination", {}).get("content")
                or []
            )
            if not isinstance(rows, list):
                rows = payload.get("results") or []
            for row in rows:
                if isinstance(row, dict):
                    yield row
            total_pages = (
                payload.get("pagination", {}).get("total_pages")
                or payload.get("total_pages")
            )
            if not rows:
                break
            if total_pages is not None and page >= int(total_pages):
                break
            page += 1

    async def _fetch_property_detail(
        self,
        public_id: str,
        *,
        mls: bool = False,
    ) -> dict[str, Any] | None:
        base = "mls_properties" if mls else "properties"
        try:
            return await self._get(f"{base}/{public_id}")
        except EasyBrokerError as exc:
            if exc.status_code == 404:
                return None
            raise

    async def _fetch_listings_enriched(
        self,
        path: str,
        *,
        mls: bool = False,
        max_pages: int = 10,
        enrich_details: bool = True,
    ) -> list[dict[str, Any]]:
        extra: dict[str, Any] = {}
        if PUBLISHED_ONLY:
            extra["search[statuses][]"] = "published"

        out: list[dict[str, Any]] = []
        async for summary in self._paginate(path, extra_params=extra, max_pages=max_pages):
            public_id = str(summary.get("public_id") or summary.get("id") or "")
            merged = dict(summary)
            if enrich_details and public_id:
                detail = await self._fetch_property_detail(public_id, mls=mls)
                if detail:
                    merged.update(detail)
            listing = easybroker_listing(merged, mls=mls)
            if listing.get("status") == "available" or not PUBLISHED_ONLY:
                out.append(listing)
        return out

    async def fetch_own_listings(self, *, max_pages: int = 10) -> list[dict[str, Any]]:
        return await self._fetch_listings_enriched("properties", mls=False, max_pages=max_pages)

    async def fetch_mls_listings(self, *, max_pages: int = 10) -> tuple[list[dict[str, Any]], bool]:
        try:
            rows = await self._fetch_listings_enriched(
                "mls_properties", mls=True, max_pages=max_pages,
            )
            return rows, False
        except EasyBrokerError as exc:
            if exc.status_code == 403 or "mls_properties" in str(exc):
                return [], True
            raise

    async def fetch_org_users(self, *, max_pages: int = 5) -> list[dict[str, Any]]:
        """GET /users — active agents in your EasyBroker organization."""
        out: list[dict[str, Any]] = []
        try:
            async for raw in self._paginate("users", max_pages=max_pages):
                out.append(easybroker_user(raw))
        except EasyBrokerError as exc:
            if exc.status_code in (403, 404):
                return []
            raise
        return out

    async def fetch_collaborations(self, *, max_pages: int = 10) -> list[dict[str, Any]]:
        """GET /collaborations — MLS partner agencies (brokers)."""
        out: list[dict[str, Any]] = []
        try:
            async for raw in self._paginate("collaborations", max_pages=max_pages):
                out.append(easybroker_collaboration(raw))
        except EasyBrokerError as exc:
            if exc.status_code in (403, 404):
                return []
            raise
        return out

    def _agents_from_listings(self, listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        agents: list[dict[str, Any]] = []
        seen: set[str] = set()
        for listing in listings:
            embedded = listing.pop("_agent", None)
            if not embedded or not isinstance(embedded, dict):
                continue
            agent = easybroker_agent(
                embedded,
                agency_name="",
            )
            agent["state"] = listing.get("state") or agent.get("state")
            agent["city"] = listing.get("city") or agent.get("city")
            nb = listing.get("neighborhood")
            if nb:
                agent["neighborhoods"] = [str(nb)]
            if agent["id"] in seen:
                continue
            seen.add(agent["id"])
            agents.append(agent)
        return agents

    @staticmethod
    def _strip_internal_fields(listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        for row in listings:
            item = {k: v for k, v in row.items() if not k.startswith("_")}
            cleaned.append(item)
        return cleaned

    async def ingest_all(
        self,
        *,
        include_mls: bool = True,
        max_pages: int = 10,
    ) -> dict[str, Any]:
        def _log(msg: str) -> None:
            print(f"[ingest-market] {msg}", flush=True)

        _log("EasyBroker: own listings …")
        listings = await self.fetch_own_listings(max_pages=max_pages)
        _log(f"EasyBroker: {len(listings)} own listings fetched")
        mls_skipped = False
        if include_mls:
            _log("EasyBroker: MLS feed …")
            mls, mls_skipped = await self.fetch_mls_listings(max_pages=max_pages)
            listings.extend(mls)
            _log(f"EasyBroker: {len(mls)} MLS listings fetched")
        else:
            _log("EasyBroker: MLS skipped")

        _log("EasyBroker: org users + collaborations …")
        collaborators = await self.fetch_org_users(max_pages=max_pages)
        brokers = await self.fetch_collaborations(max_pages=max_pages)

        listing_agents = self._agents_from_listings(listings)
        seen = {a["id"] for a in collaborators}
        for agent in listing_agents:
            if agent["id"] not in seen:
                collaborators.append(agent)
                seen.add(agent["id"])

        publishable = [r for r in listings if r.get("status") == "available"]

        return {
            "listings": self._strip_internal_fields(publishable),
            "collaborators": collaborators,
            "brokers": brokers,
            "meta": {
                "source": "easybroker",
                "mls_skipped": mls_skipped,
                "listing_count": len(publishable),
                "listing_total_raw": len(listings),
                "collaborator_count": len(collaborators),
                "broker_count": len(brokers),
                "published_only": PUBLISHED_ONLY,
            },
        }
