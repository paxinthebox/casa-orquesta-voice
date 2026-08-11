"""Live SAT lista 69 / 69-B lookups from datos abiertos CSVs."""
from __future__ import annotations

import csv
import io
import os
import re
import time
from typing import Any

import httpx

from registries.config import (
    HTTP_TIMEOUT_S,
    SAT_CACHE_DIR,
    SAT_CACHE_TTL_HOURS,
    SAT_LISTA_69_URLS,
    SAT_LISTA_69B_URLS,
)

_RFC_RE = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")


def _cache_path(name: str) -> str:
    os.makedirs(SAT_CACHE_DIR, exist_ok=True)
    return os.path.join(SAT_CACHE_DIR, name)


def _cache_fresh(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    age_h = (time.time() - os.path.getmtime(path)) / 3600
    return age_h < SAT_CACHE_TTL_HOURS


def _download(url: str, dest: str) -> bool:
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_S, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            raw = resp.content
    except Exception:
        return False
    with open(dest, "wb") as fh:
        fh.write(raw)
    return True


def _ensure_cached(url: str, filename: str) -> str | None:
    path = _cache_path(filename)
    if not _cache_fresh(path):
        if not _download(url, path):
            return path if os.path.isfile(path) else None
    return path


def _read_csv_rfc_set(path: str) -> set[str]:
    out: set[str] = set()
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("latin-1", errors="replace")
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 2:
            continue
        for cell in row[:3]:
            val = (cell or "").strip().upper()
            if _RFC_RE.match(val):
                out.add(val)
                break
    return out


def _load_index() -> dict[str, set[str]]:
    index: dict[str, set[str]] = {
        "lista_69": set(),
        "lista_efos_presuntos": set(),
        "lista_efos_definitivos": set(),
        "lista_efos_desvirtuados": set(),
    }
    for key, url in SAT_LISTA_69_URLS.items():
        path = _ensure_cached(url, f"lista_69_{key}.csv")
        if path:
            index["lista_69"] |= _read_csv_rfc_set(path)
    mapping = {
        "presuntos": "lista_efos_presuntos",
        "definitivos": "lista_efos_definitivos",
        "desvirtuados": "lista_efos_desvirtuados",
    }
    for key, url in SAT_LISTA_69B_URLS.items():
        path = _ensure_cached(url, f"lista_69b_{key}.csv")
        bucket = mapping.get(key)
        if path and bucket:
            index[bucket] |= _read_csv_rfc_set(path)
    return index


_INDEX: dict[str, set[str]] | None = None


def _get_index() -> dict[str, set[str]]:
    global _INDEX
    if _INDEX is None:
        _INDEX = _load_index()
    return _INDEX


def sat_rfc_check(rfc: str) -> dict[str, Any]:
    rfc = rfc.strip().upper()
    if not _RFC_RE.match(rfc):
        return {
            "rfc": rfc,
            "registered": False,
            "estatus_padron": "formato_invalido",
            "regimen_fiscal": None,
            "domicilio_fiscal_cp": None,
            "lista_69": False,
            "lista_efos": False,
            "lista_efos_status": None,
            "verification_token": "SAT-LIVE-INVALID",
            "source": "live",
        }

    idx = _get_index()
    on_69 = rfc in idx["lista_69"]
    efos_status = None
    lista_efos = False
    if rfc in idx["lista_efos_definitivos"]:
        lista_efos = True
        efos_status = "definitivo"
    elif rfc in idx["lista_efos_presuntos"]:
        lista_efos = True
        efos_status = "presunto"
    elif rfc in idx["lista_efos_desvirtuados"]:
        efos_status = "desvirtuado"

    return {
        "rfc": rfc,
        "registered": True,
        "estatus_padron": "activo",
        "regimen_fiscal": "consulta_listas_sat",
        "domicilio_fiscal_cp": None,
        "lista_69": on_69,
        "lista_efos": lista_efos,
        "lista_efos_status": efos_status,
        "verification_token": f"SAT-LIVE-{rfc[:8]}",
        "source": "live",
        "data_as_of": "SAT datos abiertos (listas 69 / 69-B)",
    }
