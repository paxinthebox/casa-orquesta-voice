"""
audit_agent — reviews contracts and verifies inmuebles + counterparties
against Mexican public registries (RPP, Catastro, INEGI, SAT).

Phase 1.6 status: handlers point at `tools/audit_tools.py`. Simulated
dispatch detects RFC pattern + focus_document_id + focus_listing_id and
fans out to the appropriate sub-tools.
"""
from __future__ import annotations

import os
import re

from . import Agent, Tool
from .guardrails import subagent_system
from tools.audit_tools import (
    review_promesa as _h_review_promesa,
    rpp_lookup as _h_rpp_lookup,
    catastro_lookup as _h_catastro_lookup,
    inegi_zone_stats as _h_inegi_zone_stats,
    sat_rfc_check as _h_sat_rfc_check,
)
from tools.listings_tools import get_listing as _h_get_listing
import datasets

MODEL_DIALOG = os.getenv("ANTHROPIC_MODEL_DIALOG", "accounts/fireworks/models/deepseek-v4-flash-0731")


# ---------------- Tool descriptors ----------------
review_promesa = Tool(
    name="review_promesa",
    description=(
        "Revisa una Promesa de Compraventa por cláusulas obligatorias y "
        "conformidad NOM-151."
    ),
    input_schema={
        "type": "object",
        "properties": {"document_id": {"type": "string"}},
        "required": ["document_id"],
    },
    handler=_h_review_promesa,
)

rpp_lookup = Tool(
    name="rpp_lookup",
    description=(
        "Busca un inmueble en el Registro Público de la Propiedad. "
        "Devuelve folio real, propietario inscrito y gravámenes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "state": {"type": "string"},
            "address": {"type": "string"},
            "owner_hint": {"type": "string"},
        },
        "required": ["state", "address"],
    },
    handler=_h_rpp_lookup,
)

catastro_lookup = Tool(
    name="catastro_lookup",
    description=(
        "Consulta Catastro: clave catastral, valor, predial, uso de suelo, "
        "estatus de pago."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "state": {"type": "string"},
            "address": {"type": "string"},
        },
        "required": ["state", "address"],
    },
    handler=_h_catastro_lookup,
)

inegi_zone_stats = Tool(
    name="inegi_zone_stats",
    description=(
        "Estadísticas AGEB del entorno (escuelas, ingreso, distancia al "
        "metro, índice delictivo)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "lat": {"type": "number"},
            "lng": {"type": "number"},
        },
        "required": ["lat", "lng"],
    },
    handler=_h_inegi_zone_stats,
)

sat_rfc_check = Tool(
    name="sat_rfc_check",
    description=(
        "Verifica un RFC en el padrón del SAT y en las listas 69-B y EFOS."
    ),
    input_schema={
        "type": "object",
        "properties": {"rfc": {"type": "string"}},
        "required": ["rfc"],
    },
    handler=_h_sat_rfc_check,
)


# ---------------- Agent definition ----------------
audit_agent: Agent = Agent(
    name="audit_agent",
    description=(
        "Audita contratos y verifica al inmueble y a las partes contra "
        "registros públicos (RPP, Catastro, INEGI, SAT)."
    ),
    system=subagent_system(
        "Eres audit_agent dentro de Casa·Orquesta. Tu trabajo es:\n"
        "1) Revisar Promesas de Compraventa contra las cláusulas obligatorias y la NOM-151.\n"
        "2) Verificar al inmueble en RPP (gravámenes, propietario inscrito) y "
        "   Catastro (predial, uso de suelo).\n"
        "3) Sacar contexto demográfico de INEGI cuando ayude "
        "   (escuelas, ingreso, índice delictivo).\n"
        "4) Validar contrapartes contra el padrón SAT y listas 69-B/EFOS.\n"
        "Devuelve un dictamen breve en es-MX con las banderas amarillas/rojas. "
        "Sé conciso: máximo 4 oraciones."
    ),
    tools=[
        review_promesa, rpp_lookup, catastro_lookup,
        inegi_zone_stats, sat_rfc_check,
    ],
    model=MODEL_DIALOG,
)


# ---------------- Simulated dispatch ----------------
async def _audit_simulated(self: Agent, message: str, ctx) -> dict:
    """Deterministic fan-out: review promesa if focus_document_id set;
    RPP + Catastro + INEGI if focus_listing_id set; sat_rfc_check if an
    RFC is mentioned; fall back to a 'need a target' note."""
    msg_low = message.lower()
    listing = None
    if ctx.state.get("focus_listing_id"):
        try:
            listing = await _h_get_listing(
                {"listing_id": ctx.state["focus_listing_id"]}, ctx)
        except Exception:
            listing = None

    findings: dict = {}
    bullets: list[str] = []

    rfc_match = re.search(r"\b([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})\b", message.upper())
    if rfc_match or "rfc" in msg_low or "sat" in msg_low:
        if rfc_match:
            rfc = rfc_match.group(1)
            ctx.emit("agent_tool", self.name, {"tool": "sat_rfc_check", "args": {"rfc": rfc}})
            sat = await _h_sat_rfc_check({"rfc": rfc}, ctx)
            findings["sat"] = sat
            ctx.emit("tool_result", self.name, {"tool": "sat_rfc_check", "result": sat})
            warns = []
            if sat.get("lista_69"): warns.append("⚠️ aparece en lista 69-B")
            if sat.get("lista_efos"): warns.append("⚠️ presunto en lista EFOS")
            if not warns:
                bullets.append(f"✅ SAT: {rfc} en padrón activo, régimen {sat['regimen_fiscal']}, sin alertas.")
            else:
                bullets.append(f"SAT: {rfc} {' y '.join(warns)}.")
        else:
            bullets.append("Para verificar en el SAT necesito el RFC (ej. CAS990101AAA).")

    if ctx.state.get("focus_document_id") or "promesa" in msg_low or "contrato" in msg_low:
        doc_id = ctx.state.get("focus_document_id")
        if doc_id:
            ctx.emit("agent_tool", self.name, {"tool": "review_promesa", "args": {"document_id": doc_id}})
            r = await _h_review_promesa({"document_id": doc_id}, ctx)
            findings["promesa"] = r
            if r.get("ok"):
                bullets.append(
                    f"Promesa {doc_id} cumple las "
                    f"{len(datasets.PROMESA_REQUIRED_CLAUSES)} cláusulas obligatorias "
                    f"y tiene sello NOM-151.")
            else:
                bullets.append(
                    f"Promesa {doc_id} con observaciones: "
                    f"{r.get('clause_review', {}).get('missing', []) or r.get('flags', [])}.")

    if listing:
        ctx.emit("agent_tool", self.name,
                 {"tool": "rpp_lookup",
                  "args": {"state": listing["state"], "address": listing["address"]}})
        rpp = await _h_rpp_lookup(
            {"state": listing["state"], "address": listing["address"]}, ctx)
        findings["rpp"] = rpp
        ctx.emit("tool_result", self.name, {"tool": "rpp_lookup", "result": rpp})
        ctx.emit("agent_tool", self.name,
                 {"tool": "catastro_lookup",
                  "args": {"state": listing["state"], "address": listing["address"]}})
        cat = await _h_catastro_lookup(
            {"state": listing["state"], "address": listing["address"]}, ctx)
        findings["catastro"] = cat
        ctx.emit("tool_result", self.name, {"tool": "catastro_lookup", "result": cat})
        ctx.emit("agent_tool", self.name,
                 {"tool": "inegi_zone_stats",
                  "args": {"lat": listing["lat"], "lng": listing["lng"]}})
        inegi = await _h_inegi_zone_stats(
            {"lat": listing["lat"], "lng": listing["lng"]}, ctx)
        findings["inegi"] = inegi
        ctx.emit("tool_result", self.name, {"tool": "inegi_zone_stats", "result": inegi})

        if rpp.get("encumbrances"):
            bullets.append(
                f"⚠️ RPP: hay gravamen vigente "
                f"({rpp['encumbrances'][0]['type']}, {rpp['encumbrances'][0]['creditor']}).")
        else:
            src = rpp.get("source", "")
            if src in ("live_partial", "hybrid") and not rpp.get("folio_real"):
                bullets.append(
                    "ℹ️ RPP: consulta oficial requerida — dirección geocodificada; "
                    "sin folio vía API pública.")
            else:
                bullets.append(f"✅ RPP: folio {rpp['folio_real']} sin gravámenes.")
        if cat.get("al_corriente") is False:
            bullets.append(
                f"⚠️ Catastro: último predial pagado {cat['ultimo_pago_anio']}; verificar adeudo.")
        elif cat.get("source") in ("live", "live_partial", "hybrid") and cat.get("geocode"):
            clave = cat.get("clave_catastral") or "pendiente"
            bullets.append(
                f"✅ Catastro ({cat.get('source')}): clave {clave}; "
                f"geocodificado {cat['geocode'].get('display_name', '')[:80]}.")
        else:
            bullets.append(
                f"✅ Catastro: predial al corriente, valor catastral ${cat['valor_catastral_mxn']:,}.")
        income = inegi.get("median_household_income_mxn")
        schools = inegi.get("schools_within_1km")
        crime = inegi.get("crime_index_2025")
        if income is not None:
            bullets.append(
                f"📊 INEGI: ingreso medio MXN {income:,}, "
                f"escuelas en 1 km: {schools}, "
                f"índice delictivo {crime}/5.")
        else:
            bullets.append(
                f"📊 INEGI ({inegi.get('source', 'live')}): escuelas en 1 km: {schools}, "
                f"unidades económicas 500 m: {inegi.get('economic_units_500m', '—')}.")

    if not bullets:
        bullets.append(
            "Necesito un documento o un listing para auditar. "
            "Indícame `focus_document_id` o `focus_listing_id`.")

    return {"output": " ".join(bullets), "data": findings}


audit_agent._run_simulated = _audit_simulated.__get__(audit_agent, Agent)  # type: ignore
