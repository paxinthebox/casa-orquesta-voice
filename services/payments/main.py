"""
Casa·Orquesta — Payments + CFDI Service.

Mock Stripe / Conekta intent + CFDI 4.0 emission. The CFDI XML stub mirrors the
real SAT structure closely enough that pilot brokers can verify their PAC
mapping; production swaps the stub for Facturama or Edicom.

Phase 1.4 status: ported byte-identical from MVP (no behavior changes specified).
"""
import uuid
import hashlib
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI(title="Casa·Orquesta Payments + CFDI", version="0.1.0")

payments: dict[str, dict] = {}
cfdis: dict[str, dict] = {}


class IntentReq(BaseModel):
    amount_mxn: float
    payer_id: str
    payee_id: str
    description: str = ""
    method: str = "card"  # card | spei | oxxo
    processor: str = "stripe"  # stripe | conekta | mercadopago


class CFDIReq(BaseModel):
    payment_id: str
    rfc_emisor: str = "CAS990101AAA"
    rfc_receptor: str = "XAXX010101000"
    razon_social_receptor: str = "Público en general"
    concepto: str = "Comisión por servicios inmobiliarios"
    uso_cfdi: str = "G03"  # gastos en general


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/payments/intent")
def create_intent(req: IntentReq):
    pid = f"P-{uuid.uuid4().hex[:10].upper()}"
    payments[pid] = {
        "id": pid, "amount_mxn": req.amount_mxn,
        "payer_id": req.payer_id, "payee_id": req.payee_id,
        "description": req.description, "method": req.method,
        "processor": req.processor, "status": "succeeded",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "client_secret": f"pi_{uuid.uuid4().hex}_secret_{uuid.uuid4().hex[:8]}",
    }
    return payments[pid]


@app.get("/payments/{pid}")
def get_payment(pid: str):
    p = payments.get(pid)
    if not p:
        raise HTTPException(404, "payment not found")
    return p


@app.post("/cfdi/emit")
def emit_cfdi(req: CFDIReq):
    p = payments.get(req.payment_id)
    if not p:
        raise HTTPException(404, "payment not found")
    uuid4 = str(uuid.uuid4()).upper()
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    subtotal = round(p["amount_mxn"] / 1.16, 2)
    iva = round(p["amount_mxn"] - subtotal, 2)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
                  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                  Version="4.0"
                  Folio="{uuid4[:8]}"
                  Fecha="{fecha}"
                  Sello="MOCK_SELLO_DEL_PAC"
                  FormaPago="03"
                  NoCertificado="00001000000000000000"
                  SubTotal="{subtotal:.2f}"
                  Moneda="MXN"
                  Total="{p['amount_mxn']:.2f}"
                  TipoDeComprobante="I"
                  Exportacion="01"
                  MetodoPago="PUE"
                  LugarExpedicion="06600">
  <cfdi:Emisor Rfc="{req.rfc_emisor}"
               Nombre="CASA ORQUESTA SA DE CV"
               RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="{req.rfc_receptor}"
                 Nombre="{req.razon_social_receptor}"
                 DomicilioFiscalReceptor="06600"
                 RegimenFiscalReceptor="616"
                 UsoCFDI="{req.uso_cfdi}"/>
  <cfdi:Conceptos>
    <cfdi:Concepto ClaveProdServ="80131500"
                   Cantidad="1"
                   ClaveUnidad="E48"
                   Descripcion="{req.concepto}"
                   ValorUnitario="{subtotal:.2f}"
                   Importe="{subtotal:.2f}"
                   ObjetoImp="02">
      <cfdi:Impuestos>
        <cfdi:Traslados>
          <cfdi:Traslado Base="{subtotal:.2f}"
                        Impuesto="002"
                        TipoFactor="Tasa"
                        TasaOCuota="0.160000"
                        Importe="{iva:.2f}"/>
        </cfdi:Traslados>
      </cfdi:Impuestos>
    </cfdi:Concepto>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="{iva:.2f}">
    <cfdi:Traslados>
      <cfdi:Traslado Base="{subtotal:.2f}" Impuesto="002" TipoFactor="Tasa"
                    TasaOCuota="0.160000" Importe="{iva:.2f}"/>
    </cfdi:Traslados>
  </cfdi:Impuestos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
                             Version="1.1"
                             UUID="{uuid4}"
                             FechaTimbrado="{fecha}"
                             RfcProvCertif="MOCKPAC0101000"
                             SelloCFD="MOCK_SELLO_CFD"
                             NoCertificadoSAT="00001000000000000000"
                             SelloSAT="MOCK_SELLO_SAT"/>
  </cfdi:Complemento>
</cfdi:Comprobante>"""

    cfdi_record = {
        "uuid": uuid4, "payment_id": req.payment_id,
        "rfc_emisor": req.rfc_emisor, "rfc_receptor": req.rfc_receptor,
        "subtotal": subtotal, "iva": iva, "total": p["amount_mxn"],
        "status": "vigente", "fecha_timbrado": fecha,
        "xml_sha256": hashlib.sha256(xml.encode()).hexdigest(),
        "xml": xml,
    }
    cfdis[uuid4] = cfdi_record
    return {k: v for k, v in cfdi_record.items() if k != "xml"}


@app.get("/cfdi/{uuid_}")
def get_cfdi(uuid_: str):
    c = cfdis.get(uuid_)
    if not c:
        raise HTTPException(404, "cfdi not found")
    return {k: v for k, v in c.items() if k != "xml"}


@app.get("/cfdi/{uuid_}/xml")
def get_cfdi_xml(uuid_: str):
    c = cfdis.get(uuid_)
    if not c:
        raise HTTPException(404, "cfdi not found")
    return Response(content=c["xml"], media_type="application/xml")


@app.get("/cfdi")
def list_cfdis():
    return [{k: v for k, v in c.items() if k != "xml"} for c in cfdis.values()]


@app.get("/payments")
def list_payments():
    return list(payments.values())


# ============================================================================
# Phase 4.6 — Cross-service auth + DSAR wiring
# ============================================================================
import os as _os
import sys as _sys                                  # noqa: E402
_SHARED = _os.path.normpath(
    _os.path.join(_os.path.dirname(__file__), "..", "_shared")
)
if _SHARED not in _sys.path:
    _sys.path.insert(0, _SHARED)

try:
    from auth_middleware import AuthInjector                  # noqa: E402
    from dsar_responder import mount_dsar                     # noqa: E402
    _DSAR_READY = True
except Exception as _e:                                       # pragma: no cover
    print(f"[payments] DSAR wiring deferred: {_e!r}")
    _DSAR_READY = False


# Phase 5.1 — Protocol-driven store wires the DSAR contract.
from store import (                                          # noqa: E402
    build_default_store as _build_payments_store,
)

_payments_store = _build_payments_store()


def set_payments_store(store) -> None:
    """Test hook — inject the in-memory store with pinned ids/time."""
    global _payments_store
    _payments_store = store


def get_payments_store():
    return _payments_store


async def _payments_dsar_gather(user_id: str, tenant_id: str) -> dict:
    return await _payments_store.gather_user_data(
        tenant_id=tenant_id, user_id=user_id,
    )


async def _payments_dsar_purge(user_id: str, tenant_id: str) -> dict:
    return await _payments_store.purge_user_data(
        tenant_id=tenant_id, user_id=user_id,
    )


def _payments_dsar_gather_legacy(user_id: str, tenant_id: str) -> dict:
    """Pre-P5.1 in-line code. Kept for reference; the wiring at the
    bottom of this file now uses the Protocol-driven path above."""
    mine_payments = [
        p for p in payments.values()
        if p.get("payer_id") == user_id or p.get("payee_id") == user_id
    ]
    mine_cfdis = [
        c for c in cfdis.values()
        if (c.get("payment", {}) or {}).get("payer_id") == user_id
    ]
    return {
        "payments": mine_payments,
        "cfdis": mine_cfdis,
        "summary": {
            "payment_count": len(mine_payments),
            "cfdi_count": len(mine_cfdis),
            "retention_note": "CFDIs retained 5y per SAT requirements",
        },
    }


def _payments_dsar_purge_legacy(user_id: str, tenant_id: str) -> dict:
    """Pre-P5.1 in-line code. Kept for reference; the wiring at the
    bottom of this file now uses the Protocol-driven async version
    defined earlier. Same legal hold semantics — CFDI 4.0 + SAT records
    must be kept 5 years."""
    intents_dropped = 0
    payments_redacted = 0
    cfdis_redacted = 0
    for pid, p in list(payments.items()):
        if p.get("payer_id") != user_id and p.get("payee_id") != user_id:
            continue
        if (p.get("status") or "").lower() in ("intent", "draft"):
            payments.pop(pid, None)
            intents_dropped += 1
        else:
            if p.get("payer_id") == user_id:
                p["payer_id"] = "redacted-by-dsar"
            if p.get("payee_id") == user_id:
                p["payee_id"] = "redacted-by-dsar"
            payments_redacted += 1
    for cid, c in list(cfdis.items()):
        pay = c.get("payment", {}) or {}
        if pay.get("payer_id") == user_id:
            pay["payer_id"] = "redacted-by-dsar"
            c["payment"] = pay
            cfdis_redacted += 1
    return {
        "count": intents_dropped + payments_redacted + cfdis_redacted,
        "deleted": True,
        "detail": {
            "intents_dropped": intents_dropped,
            "payments_redacted": payments_redacted,
            "cfdis_redacted": cfdis_redacted,
            "retention_note": "SAT/CFDI legal hold honored",
        },
    }


if _DSAR_READY:
    app.add_middleware(AuthInjector)
    mount_dsar(
        app,
        gather=_payments_dsar_gather,
        purge=_payments_dsar_purge,
        service_name="payments",
    )
