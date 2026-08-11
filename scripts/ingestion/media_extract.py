"""Extract listing photo URLs from portal / Apify payloads."""
from __future__ import annotations

import re
from typing import Any

_NAVENT_URL_RE = re.compile(
    r"https?://(?:img\d+|preprostatic)\.naventcdn\.com/\S+",
    re.IGNORECASE,
)
_HTTP_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
# Portal HTML pages are not photos (juandiaz actor puts listing URLs in thumbnail).
_LISTING_PAGE_RE = re.compile(
    r"inmuebles24\.com/propiedades/|zonaprop\.com\.mx/propiedades/|"
    r"vivanuncios\.com\.mx/.*\.html|\.html(?:\?|$)",
    re.IGNORECASE,
)
_IMAGE_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp|gif|avif)(?:\?|$)", re.IGNORECASE)

# Prefer larger photos for cards; fall back to smaller variants.
_PICTURE_SIZE_KEYS = (
    "url1200x1200",
    "resizeUrl1200x1200",
    "url730x532",
    "url720x532",
    "url360x266",
    "url215x159",
    "url100x75",
    "url",
    "src",
    "original",
    "thumb",
)


def is_listing_image_url(url: str) -> bool:
    """True when URL points at a photo, not a portal listing HTML page."""
    cleaned = url.strip()
    if not cleaned.startswith("http"):
        return False
    lower = cleaned.lower()
    if _LISTING_PAGE_RE.search(lower):
        return False
    if "naventcdn.com" in lower:
        return True
    if _IMAGE_EXT_RE.search(lower):
        return True
    # Known CDNs used by MLS / portal scrapers.
    if any(
        host in lower
        for host in (
            "cloudinary.com",
            "easybroker.com",
            "staticw2.yolacdn.com",
            "images.unsplash.com",
            "picsum.photos",
        )
    ):
        return True
    return False


def _clean_url(raw: str) -> str | None:
    url = raw.strip().rstrip(".,;)")
    if not url.startswith("http"):
        return None
    # Strip broken double-query tails from some Navent payloads.
    if url.count("?") > 1:
        url = url.split("?", 1)[0]
    if not is_listing_image_url(url):
        return None
    return url


def _urls_from_picture(picture: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in _PICTURE_SIZE_KEYS:
        val = picture.get(key)
        if isinstance(val, str):
            cleaned = _clean_url(val)
            if cleaned:
                out.append(cleaned)
    for val in picture.values():
        if isinstance(val, str):
            for match in _NAVENT_URL_RE.findall(val):
                cleaned = _clean_url(match)
                if cleaned:
                    out.append(cleaned)
    return out


def _walk(value: Any, out: list[str]) -> None:
    if isinstance(value, str):
        if value.startswith("http"):
            cleaned = _clean_url(value)
            if cleaned and (
                "naventcdn.com" in cleaned
                or any(ext in cleaned.lower() for ext in (".jpg", ".jpeg", ".png", ".webp"))
            ):
                out.append(cleaned)
        else:
            for match in _HTTP_URL_RE.findall(value):
                cleaned = _clean_url(match)
                if cleaned and "naventcdn.com" in cleaned:
                    out.append(cleaned)
        return
    if isinstance(value, dict):
        if "multimediaTypeId" in value or any(k.startswith("url") for k in value):
            out.extend(_urls_from_picture(value))
        for child in value.values():
            _walk(child, out)
        return
    if isinstance(value, list):
        for item in value:
            _walk(item, out)


def sanitize_listing_media(row: dict[str, Any]) -> None:
    """Drop non-image portal URLs from media/thumbnail in place."""
    media = row.get("media")
    if isinstance(media, list):
        cleaned = [u for u in media if isinstance(u, str) and is_listing_image_url(u)]
    else:
        cleaned = []
    thumb = row.get("thumbnail")
    if isinstance(thumb, str) and is_listing_image_url(thumb):
        if thumb not in cleaned:
            cleaned.insert(0, thumb)
    if cleaned:
        row["media"] = cleaned[:12]
        row["thumbnail"] = cleaned[0]
    else:
        row.pop("media", None)
        row.pop("thumbnail", None)


def extract_listing_media(raw: dict[str, Any]) -> list[str]:
    """Return deduped image URLs, largest preferred."""
    found: list[str] = []

    for key in (
        "picture",
        "mainPicture",
        "coverPicture",
        "coverPhoto",
        "mainImage",
        "house_image",
        "thumbnail",
        "image",
        "imageUrl",
        "photo",
        "photoUrl",
    ):
        val = raw.get(key)
        if isinstance(val, dict):
            found.extend(_urls_from_picture(val))
        elif isinstance(val, str):
            cleaned = _clean_url(val)
            if cleaned:
                found.append(cleaned)

    for key in ("images", "photos", "media", "pictures", "imageUrls", "multimedia"):
        val = raw.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    cleaned = _clean_url(item)
                    if cleaned:
                        found.append(cleaned)
                elif isinstance(item, dict):
                    found.extend(_urls_from_picture(item))
        elif isinstance(val, dict):
            _walk(val, found)

    visible = raw.get("visible_pictures")
    if isinstance(visible, dict):
        pics = visible.get("pictures")
        if isinstance(pics, list):
            for item in pics:
                if isinstance(item, dict):
                    found.extend(_urls_from_picture(item))

    _walk(raw, found)

    seen: set[str] = set()
    unique: list[str] = []
    for url in found:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique[:12]
