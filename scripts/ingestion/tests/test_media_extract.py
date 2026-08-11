"""Tests for listing media URL extraction."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INGEST = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, INGEST)

from media_extract import extract_listing_media, is_listing_image_url, sanitize_listing_media  # noqa: E402


def test_picture_object_navent():
    raw = {
        "title": "Casa en Xochitepec",
        "picture": {
            "multimediaTypeId": 2,
            "url1200x1200": (
                "https://img10.naventcdn.com/avisos/11/00/63/76/44/83/"
                "1200x1200/1143679410.jpg?isFirstImage=true"
            ),
            "url360x266": (
                "https://img10.naventcdn.com/avisos/11/00/63/76/44/83/"
                "360x266/1143679410.jpg"
            ),
        },
    }
    media = extract_listing_media(raw)
    assert len(media) >= 1
    assert media[0].startswith("https://img10.naventcdn.com/")


def test_images_list():
    raw = {
        "images": [
            "https://cdn.example.com/a.jpg",
            {"url": "https://cdn.example.com/b.jpg"},
        ],
    }
    media = extract_listing_media(raw)
    assert "https://cdn.example.com/a.jpg" in media
    assert "https://cdn.example.com/b.jpg" in media


def test_empty_when_no_media():
    assert extract_listing_media({"title": "Sin fotos"}) == []


def test_rejects_inmuebles24_listing_page_url():
    page = (
        "https://www.inmuebles24.com/propiedades/clasificado/"
        "veclapin-departamento-colonia-centro-149646843.html?n_src=Listado"
    )
    assert not is_listing_image_url(page)
    assert extract_listing_media({"thumbnail": page, "url": page}) == []


def test_sanitize_listing_media_strips_page_urls():
    row = {
        "id": "I24-1",
        "thumbnail": "https://www.inmuebles24.com/propiedades/clasificado/x-123.html",
        "media": [
            "https://www.inmuebles24.com/propiedades/clasificado/x-123.html",
            "https://img10.naventcdn.com/avisos/1/2/3.jpg",
        ],
    }
    sanitize_listing_media(row)
    assert row["media"] == ["https://img10.naventcdn.com/avisos/1/2/3.jpg"]
    assert row["thumbnail"] == row["media"][0]


def test_azzouzana_house_image():
    raw = {
        "postingId": "148695443",
        "url": "https://www.inmuebles24.com/propiedades/clasificado/veclcain-casa-148695443.html",
        "house_image": "https://img10.naventcdn.com/avisos/18/01/48/69/54/43/720x532/1582687338.jpg",
        "images": [
            "https://img10.naventcdn.com/avisos/18/01/48/69/54/43/720x532/1582687338.jpg?isFirstImage=true",
            "https://img10.naventcdn.com/avisos/18/01/48/69/54/43/720x532/1582687345.jpg",
        ],
    }
    media = extract_listing_media(raw)
    assert media[0].startswith("https://img10.naventcdn.com/")
    assert len(media) >= 2
