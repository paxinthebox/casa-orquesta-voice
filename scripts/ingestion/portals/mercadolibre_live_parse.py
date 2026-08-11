"""Parse Mercado Libre Inmuebles MX SERP HTML for live ingest (mirrors actor parse.js)."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

_MLM_RE = re.compile(r"MLM-(\d{6,})", re.I)
_ITEMS_PER_PAGE = 48


def item_id_from_url(url: str) -> str:
    if not url:
        return ""
    match = _MLM_RE.search(url.strip())
    return match.group(1) if match else ""


def canonical_url(url: str) -> str:
    return url.strip().split("#")[0].split("?")[0]


def listing_mode(start_url: str) -> str:
    return "rent" if "/renta/" in start_url.lower() else "sale"


def property_type_from_url(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return "inmueble"
    host = host.lower()
    if host.startswith("departamento."):
        return "departamento"
    if host.startswith("casa."):
        return "casa"
    return "inmueble"


def normalize_state(raw: str) -> str:
    folded = raw.strip().lower()
    if folded in {"cdmx", "ciudad de mexico", "ciudad de méxico", "distrito federal", "df"}:
        return "CDMX"
    if folded == "morelos":
        return "Morelos"
    return raw.strip()


def split_location(location: str) -> dict[str, str]:
    parts = [part.strip() for part in location.split(",") if part.strip()]
    if len(parts) >= 3:
        return {
            "neighborhood": parts[0],
            "city": parts[1],
            "state": normalize_state(parts[2]),
        }
    if len(parts) == 2:
        return {"neighborhood": parts[0], "city": parts[1], "state": ""}
    if len(parts) == 1:
        return {"neighborhood": parts[0], "city": "", "state": ""}
    return {"neighborhood": "", "city": "", "state": ""}


def parse_price(text: str) -> int:
    raw = re.sub(r"\s+", " ", text)
    match = re.search(r"(?:MXN|\$)\s*([\d.,]+)", raw, re.I) or re.search(
        r"([\d]{1,3}(?:[.,]\d{3})+)", raw
    )
    if not match:
        return 0
    digits = re.sub(r"[.,](?=\d{3}\b)", "", match.group(1)).replace(",", "")
    try:
        return int(digits)
    except ValueError:
        return 0


def parse_int_attr(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def page_url(start_url: str, page: int) -> str:
    base = start_url if start_url.endswith("/") else f"{start_url}/"
    parsed = urlparse(base)
    stripped = re.sub(r"/_Desde_\d+/?$", "", parsed.path).rstrip("/")
    if page <= 1:
        return f"{parsed.scheme}://{parsed.netloc}{stripped}/"
    offset = (page - 1) * _ITEMS_PER_PAGE + 1
    return f"{parsed.scheme}://{parsed.netloc}{stripped}/_Desde_{offset}/"


def paginate_urls(start_url: str, *, max_pages: int = 10) -> list[str]:
    return [page_url(start_url, page) for page in range(1, max_pages + 1)]


def _card_html_chunks(html: str) -> list[str]:
    """Split SERP into per-card HTML chunks via listing permalinks."""
    pattern = re.compile(
        r'href="(https?://[^"]*mercadolibre\.com\.mx/MLM-\d{6,}[^"]*_JM[^"]*)"',
        re.I,
    )
    matches = list(pattern.finditer(html))
    if not matches:
        return []
    chunks: list[str] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(html)
        chunks.append(html[start:end])
    return chunks


def _title_from_chunk(chunk: str) -> str:
    match = re.search(
        r'href="https?://[^"]*mercadolibre\.com\.mx/MLM-\d{6,}[^"]*_JM[^"]*">\s*([^<]+)\s*</a>',
        chunk,
        re.I | re.S,
    )
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def parse_serp_html(html: str, *, start_url: str = "") -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for chunk in _card_html_chunks(html):
        link_match = re.search(
            r'href="(https?://[^"]*mercadolibre\.com\.mx/MLM-\d{6,}[^"]*_JM)',
            chunk,
            re.I,
        )
        if not link_match:
            continue
        href = canonical_url(link_match.group(1))
        if "/click." in href or "/registration" in href:
            continue
        item_id = item_id_from_url(href)
        if not item_id or item_id in seen:
            continue
        title = _title_from_chunk(chunk)
        if len(title) < 8:
            continue
        seen.add(item_id)

        card_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", chunk))
        loc_match = re.search(
            r"([A-Za-zÁÉÍÓÚáéíóúñÑ][A-Za-zÁÉÍÓÚáéíóúñÑ0-9 .'-]*,\s*[A-Za-zÁÉÍÓÚáéíóúñÑ .'-]+,\s*"
            r"(?:Distrito Federal|CDMX|Morelos|Ciudad de México))\b",
            card_text,
        )
        location = loc_match.group(1).strip() if loc_match else ""
        geo = split_location(location)
        img_match = re.search(r'src="(https?://[^"]*mlstatic[^"]+)"', chunk, re.I)

        rows.append({
            "item_id": item_id,
            "url": href,
            "title": title,
            "description": "",
            "location": location,
            "neighborhood": geo["neighborhood"],
            "city": geo["city"],
            "state": geo["state"],
            "price": parse_price(card_text),
            "currency": "MXN",
            "bedrooms": parse_int_attr(card_text, r"(\d+)\s*rec(?:ámaras?|\.?)?"),
            "bathrooms": parse_int_attr(card_text, r"(\d+)\s*ba[nñ]os?"),
            "area_m2": parse_int_attr(card_text, r"(\d+)\s*m²"),
            "property_type": property_type_from_url(href),
            "listing_mode": listing_mode(start_url),
            "lat": None,
            "lng": None,
            "thumbnail": img_match.group(1) if img_match else None,
        })
    return rows
