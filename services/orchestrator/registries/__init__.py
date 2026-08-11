"""
Public registry connectors — live sources with mock fallback.

REGISTRY_MODE:
  mock  — deterministic demo data (tests / offline)
  live  — live only; raises if unavailable
  auto  — try live, fall back to mock (default)
"""
from __future__ import annotations

from typing import Any

from registries import mock as mock_registry
from registries.config import require_live_only, use_live


def _merge_hybrid(live: dict[str, Any], filled: dict[str, Any]) -> dict[str, Any]:
    out = {**filled, **live}
    out["source"] = "hybrid"
    out["live_fields"] = list(live.get("live_fields") or live.keys())
    return out


def _finalize(result: dict[str, Any], *, fallback_reason: str | None = None) -> dict[str, Any]:
    if fallback_reason:
        result = {**result, "fallback_reason": fallback_reason}
    return result


def rpp_lookup(state: str, address: str, owner_hint: str | None = None) -> dict[str, Any]:
    if use_live():
        try:
            from registries import rpp_live

            live = rpp_live.rpp_lookup(state, address, owner_hint)
            if live and live.get("source") == "live":
                return live
            if live and live.get("source") == "live_partial":
                if require_live_only():
                    return live
                mock = mock_registry.rpp_lookup(state, address, owner_hint)
                return _finalize(
                    {**mock, **{k: v for k, v in live.items() if v is not None},
                     "source": "hybrid", "live_overlay": live},
                    fallback_reason="rpp_partial_no_public_api",
                )
        except Exception:
            if require_live_only():
                raise
    mock = mock_registry.rpp_lookup(state, address, owner_hint)
    return _finalize(mock, fallback_reason="live_unavailable")


def catastro_lookup(state: str, address: str) -> dict[str, Any]:
    if use_live():
        try:
            from registries import catastro_live

            live = catastro_live.catastro_lookup(state, address)
            if live and live.get("source") == "live":
                return live
            if live and live.get("source") == "live_partial":
                if require_live_only():
                    return live
                mock = mock_registry.catastro_lookup(state, address)
                merged = {**mock, **live, "source": "hybrid"}
                merged["valor_catastral_mxn"] = live.get("valor_catastral_mxn") or mock["valor_catastral_mxn"]
                merged["al_corriente"] = mock["al_corriente"] if live.get("al_corriente") is None else live["al_corriente"]
                return _finalize(merged, fallback_reason="catastro_partial")
        except Exception:
            if require_live_only():
                raise
    mock = mock_registry.catastro_lookup(state, address)
    return _finalize(mock, fallback_reason="live_unavailable")


def inegi_zone_stats(lat: float, lng: float) -> dict[str, Any]:
    if use_live():
        try:
            from registries import inegi_live

            live = inegi_live.inegi_zone_stats(lat, lng)
            if live:
                if require_live_only() and live.get("population") is None:
                    return live
                mock = mock_registry.inegi_zone_stats(lat, lng)
                if live.get("population") is None:
                    return _merge_hybrid(live, mock)
                return live
        except Exception:
            if require_live_only():
                raise
    mock = mock_registry.inegi_zone_stats(lat, lng)
    return _finalize(mock, fallback_reason="live_unavailable")


def sat_rfc_check(rfc: str) -> dict[str, Any]:
    if use_live():
        try:
            from registries import sat_live

            live = sat_live.sat_rfc_check(rfc)
            if live:
                return live
        except Exception:
            if require_live_only():
                raise
    mock = mock_registry.sat_rfc_check(rfc)
    return _finalize(mock, fallback_reason="live_unavailable")
