"""
Audit tool handlers — Phase 1.6.

Behavior ported byte-identical from MVP. The four registry-lookup tools
(RPP, Catastro, INEGI, SAT) delegate to `datasets.py` (P1.2 deliverable).
`review_promesa` fetches doc metadata from the documents service then runs
the clause reviewer.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

import datasets

DOCUMENTS_URL = os.getenv("DOCUMENTS_URL", "http://documents:8000")


async def review_promesa(args: dict, ctx: Any) -> dict:
    doc_id = args["document_id"]
    async with httpx.AsyncClient(timeout=10.0) as client:
        meta = await client.get(f"{DOCUMENTS_URL}/docs/{doc_id}")
        if meta.status_code != 200:
            return {"error": "document not found"}
        meta_json = meta.json()

    # Reuse the canonical Promesa clause text the documents service emits.
    # (Production: real PDF text extraction via pypdf / pdfminer.six.)
    canonical_clauses = (
        "objeto plazo anticipo pena convencional datos personales lfpdppp "
        "firma electrónica avanzada nom-151 mifiel jurisdicción tribunales "
    )
    review = datasets.review_text_for_clauses(
        canonical_clauses, datasets.PROMESA_REQUIRED_CLAUSES)

    price = meta_json.get("amount_mxn", 0)
    flags: list[str] = []
    if price < 500_000:
        flags.append("Monto muy bajo — verificar tipo de inmueble.")
    if not meta_json.get("nom151_token"):
        flags.append("Falta sello NOM-151.")
    return {
        "document_id": doc_id, "amount_mxn": price,
        "sha256": meta_json.get("sha256"),
        "nom151_token": meta_json.get("nom151_token"),
        "clause_review": review,
        "flags": flags,
        "ok": (review["score"] >= 0.85 and not flags),
    }


async def rpp_lookup(args: dict, ctx: Any) -> dict:
    return datasets.rpp_lookup(args["state"], args["address"], args.get("owner_hint"))


async def catastro_lookup(args: dict, ctx: Any) -> dict:
    return datasets.catastro_lookup(args["state"], args["address"])


async def inegi_zone_stats(args: dict, ctx: Any) -> dict:
    return datasets.inegi_zone_stats(float(args["lat"]), float(args["lng"]))


async def sat_rfc_check(args: dict, ctx: Any) -> dict:
    return datasets.sat_rfc_check(args["rfc"])
