"""Build GET /listings query params for hybrid search."""
from __future__ import annotations

import os
import sys

_SHARED = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "_shared"))
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

from location_filters import location_needles  # noqa: E402
from near_match import _scoped_pilot_states  # noqa: E402

# Headroom so near-match can still surface slightly-over-budget rows
# without starving the candidate pool of in-budget colonia inventory.
_PRICE_FETCH_FACTOR = float(os.getenv("MATCHING_NEAR_PRICE_FACTOR", "1.5"))


def location_scoped_search(filters: dict, query: str) -> bool:
    """True when the user named a city/colonia/zone — keep all types for local near-match."""
    if filters.get("city") or filters.get("neighborhood") or filters.get("zone"):
        return True
    return bool(location_needles(filters=filters, query=query))


def build_listings_fetch_params(
    filters: dict,
    *,
    query: str,
    candidate_limit: int,
) -> dict:
    """HTTP query params for GET /listings — city-scoped when user named a municipio.

    Always forward type / beds / budget when present. Omitting them under a
    municipio scope used to fill the balanced pool with newest (often
    over-budget or beds=0) rows and starve profile-matching inventory.
    """
    scoped = dict(filters)
    params: dict = {"limit": candidate_limit, "balance_sources": True}
    pilot_states = scoped.get("states")
    if isinstance(pilot_states, list):
        pilot_states = _scoped_pilot_states(
            pilot_states, filters=scoped, query=query,
        )
        if pilot_states and len(pilot_states) == 1:
            scoped["state"] = pilot_states[0]
            scoped.pop("states", None)
            pilot_states = None
    if isinstance(scoped.get("states"), list) and scoped.get("states"):
        pass
    elif scoped.get("state"):
        params["state"] = scoped["state"]

    scoped_city = str(
        scoped.get("municipality") or scoped.get("city") or "",
    ).strip()
    if scoped.get("state") == "CDMX":
        # Listings store alcaldía in ``city`` (e.g. Coyoacán), not "Ciudad de México".
        mun = str(scoped.get("municipality") or "").strip()
        if mun:
            scoped_city = mun
    if scoped_city and location_scoped_search(scoped, query):
        params["city"] = scoped_city

    # Prefer exact type in the SQL candidate pool; near-match can still widen
    # type in-memory when the strict pool is empty.
    if scoped.get("type"):
        params["type"] = str(scoped["type"])
    elif isinstance(scoped.get("types"), list) and len(scoped["types"]) == 1:
        params["type"] = str(scoped["types"][0])

    # Do NOT push beds_min into SQL: Vivanuncios (and some other portal rows)
    # store beds=0 when unknown. Matching applies beds in-memory and treats
    # missing/0 as unknown so those sources can still surface.

    price = scoped.get("price_max_mxn")
    if isinstance(price, (int, float)) and int(price) > 0:
        params["price_max_mxn"] = int(int(price) * _PRICE_FETCH_FACTOR)

    if scoped.get("listing_mode"):
        params["listing_mode"] = scoped["listing_mode"]
    return params
