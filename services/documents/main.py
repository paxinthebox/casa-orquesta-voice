"""
Casa·Orquesta — Documents Service.

Generates Promesa de Compraventa as PDF and tracks Mifiel-style e-signature lifecycle.
NOM-151 timestamp is mocked — production would call Mifiel's PSC API.

Phase 1.4 status: ported from MVP with PDF persistence moved to S3/Tigris.
When S3_ENDPOINT + S3_BUCKET + S3_ACCESS_KEY + S3_SECRET_KEY are set, PDFs are
uploaded to S3 and served via signed URLs. When unset (dev mode), files land in
/app/out and are served via FileResponse — the MVP behavior. Metadata (and the
local `documents` dict cache) is unchanged; Phase 1.4-listings work will move
this to Postgres in a later sweep.
"""
import os
import uuid
import hashlib
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

OUT_DIR = "/app/out"
os.makedirs(OUT_DIR, exist_ok=True)

LISTINGS_URL = os.getenv("LISTINGS_URL", "http://listings:8000")

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_REGION = os.getenv("S3_REGION", "auto")
S3_SIGNED_URL_TTL = int(os.getenv("S3_SIGNED_URL_TTL", "3600"))

S3_ENABLED = bool(S3_ENDPOINT and S3_BUCKET and S3_ACCESS_KEY and S3_SECRET_KEY)

app = FastAPI(title="Casa·Orquesta Documents", version="0.1.0")

documents: dict[str, dict] = {}


# ----------------------- S3 / Tigris adapter -----------------------
class _S3Client:
    def __init__(self):
        self._client = None
        if S3_ENABLED:
            try:
                import boto3  # type: ignore
                self._client = boto3.client(
                    "s3",
                    endpoint_url=S3_ENDPOINT,
                    aws_access_key_id=S3_ACCESS_KEY,
                    aws_secret_access_key=S3_SECRET_KEY,
                    region_name=S3_REGION,
                )
            except Exception as e:
                print(f"[documents] S3 client init failed; falling back to local: {e}")
                self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def upload(self, key: str, local_path: str) -> None:
        if not self._client:
            return
        with open(local_path, "rb") as f:
            self._client.put_object(
                Bucket=S3_BUCKET, Key=key, Body=f.read(),
                ContentType="application/pdf",
            )

    def signed_url(self, key: str) -> str:
        if not self._client:
            return ""
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": key},
            ExpiresIn=S3_SIGNED_URL_TTL,
        )


s3 = _S3Client()


class PromesaReq(BaseModel):
    listing_id: str
    buyer_id: str
    amount_mxn: int
    plazo_dias: int = 60


class SignReq(BaseModel):
    document_id: str
    signer_role: str  # "buyer" | "owner" | "realtor"
    signer_id: str


def num_to_words_mxn(n: int) -> str:
    """Very rough number-to-words for legal docs (Spanish)."""
    if n == 0:
        return "cero pesos"
    millones = n // 1_000_000
    miles = (n % 1_000_000) // 1_000
    pesos = n % 1_000
    parts = []
    if millones:
        parts.append(f"{millones} millones")
    if miles:
        parts.append(f"{miles} mil")
    if pesos:
        parts.append(f"{pesos}")
    return " ".join(parts) + " pesos M.N."


@app.get("/health")
def health():
    return {"status": "ok", "storage": "s3" if s3.enabled else "local"}


@app.post("/docs/promesa")
async def generate_promesa(req: PromesaReq):
    # Fetch listing
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{LISTINGS_URL}/listings/{req.listing_id}")
        if r.status_code != 200:
            raise HTTPException(404, "listing not found")
        listing = r.json()

    doc_id = f"D-{uuid.uuid4().hex[:10].upper()}"
    pdf_path = os.path.join(OUT_DIR, f"{doc_id}.pdf")

    # Build PDF
    pdf = SimpleDocTemplate(pdf_path, pagesize=letter,
                            leftMargin=72, rightMargin=72, topMargin=72, bottomMargin=72)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], alignment=1, fontSize=16, spaceAfter=12, textColor=colors.HexColor("#1E2761"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceAfter=8, textColor=colors.HexColor("#1E2761"))
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=11, leading=15, spaceAfter=8)
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=9, textColor=colors.grey)

    story = []
    story.append(Paragraph("PROMESA DE COMPRAVENTA", h1))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Folio: {doc_id} &nbsp;|&nbsp; Fecha: {datetime.now().strftime('%d/%m/%Y')}", small))
    story.append(Spacer(1, 14))

    story.append(Paragraph("CONTRATO DE PROMESA DE COMPRAVENTA QUE CELEBRAN POR UNA PARTE EL <b>FUTURO COMPRADOR</b> "
                           f"identificado como {req.buyer_id}, y POR LA OTRA PARTE EL <b>FUTURO VENDEDOR</b> "
                           f"propietario del inmueble identificado como {listing.get('id')}; "
                           "AL TENOR DE LAS SIGUIENTES DECLARACIONES Y CLÁUSULAS:", body))

    story.append(Paragraph("DECLARACIONES", h2))
    story.append(Paragraph(f"<b>I.</b> Que el inmueble objeto del presente contrato se ubica en: "
                           f"<i>{listing.get('address')}</i>, con una superficie de {listing.get('m2')} m², "
                           f"contando con {listing.get('beds')} recámaras y {listing.get('baths')} baños.", body))
    story.append(Paragraph("<b>II.</b> Que el FUTURO VENDEDOR manifiesta ser el legítimo propietario del inmueble "
                           "y que se encuentra al corriente en el pago de servicios e impuestos.", body))
    story.append(Paragraph("<b>III.</b> Que las partes manifiestan su consentimiento libre, expreso y sin vicios "
                           "para celebrar el presente contrato, en términos de los artículos 2243 y siguientes del "
                           "Código Civil aplicable.", body))

    story.append(Paragraph("CLÁUSULAS", h2))
    monto_letras = num_to_words_mxn(req.amount_mxn)
    cl_data = [
        ("PRIMERA — Objeto",
         f"El FUTURO COMPRADOR se obliga a adquirir y el FUTURO VENDEDOR a transmitir la propiedad del "
         f"inmueble descrito por la cantidad de <b>${req.amount_mxn:,.2f} M.N.</b> "
         f"({monto_letras})."),
        ("SEGUNDA — Plazo",
         f"Las partes se obligan a celebrar el contrato definitivo de compraventa dentro de un plazo "
         f"máximo de <b>{req.plazo_dias} días naturales</b> contados a partir de la firma del presente."),
        ("TERCERA — Anticipo",
         f"El FUTURO COMPRADOR entrega en este acto la cantidad de <b>${int(req.amount_mxn*0.05):,.2f} M.N.</b> "
         f"como anticipo, mismo que se aplicará al precio total al firmarse el contrato definitivo."),
        ("CUARTA — Condiciones",
         "El presente contrato queda sujeto al avalúo bancario y, en su caso, a la autorización del crédito "
         "hipotecario por parte de la institución correspondiente (INFONAVIT, FOVISSSTE o entidad bancaria)."),
        ("QUINTA — Pena Convencional",
         "En caso de incumplimiento, la parte que incumpla pagará a la otra una pena convencional equivalente "
         "al 10% del precio pactado, sin perjuicio de exigir el cumplimiento forzoso."),
        ("SEXTA — Datos Personales (LFPDPPP)",
         "Las partes reconocen que sus datos personales serán tratados conforme al Aviso de Privacidad de "
         "Casa·Orquesta, en términos de la Ley Federal de Protección de Datos Personales en Posesión de los "
         "Particulares."),
        ("SÉPTIMA — Firma Electrónica",
         "Las partes aceptan firmar el presente contrato mediante firma electrónica avanzada con sello digital "
         "de tiempo conforme a la NOM-151-SCFI-2016, a través del prestador de servicios de certificación Mifiel."),
        ("OCTAVA — Jurisdicción",
         f"Para la interpretación y cumplimiento del presente contrato, las partes se someten a los tribunales "
         f"competentes de {listing.get('state', 'CDMX')}, renunciando a cualquier otro fuero."),
    ]
    for title, text in cl_data:
        story.append(Paragraph(f"<b>{title}.</b> {text}", body))

    story.append(Spacer(1, 24))
    story.append(Paragraph("Firmas — bloque de firma electrónica avanzada (Mifiel)", h2))
    sig_table = Table([
        ["FUTURO COMPRADOR", "FUTURO VENDEDOR", "TESTIGO / AGENTE"],
        [req.buyer_id, listing.get("owner_id", "—"), "Casa·Orquesta"],
        ["[Pendiente firma]", "[Pendiente firma]", "[Pendiente firma]"],
    ], colWidths=[2.0 * inch, 2.0 * inch, 2.0 * inch])
    sig_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2FF")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1E2761")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(sig_table)
    story.append(Spacer(1, 12))
    story.append(Paragraph("Documento generado automáticamente por Casa·Orquesta. Pendiente de firma electrónica avanzada NOM-151. "
                           "Este Folio podrá verificarse en la plataforma de Mifiel mediante el código incluido al pie.",
                           small))
    pdf.build(story)

    # Hash + mock NOM-151 timestamp
    with open(pdf_path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    timestamp_token = f"NOM151-MOCK-{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}-{sha[:8]}"

    # Optional upload to S3/Tigris
    s3_key = ""
    if s3.enabled:
        s3_key = f"promesas/{doc_id}.pdf"
        try:
            s3.upload(s3_key, pdf_path)
        except Exception as e:
            print(f"[documents] S3 upload failed; keeping local fallback: {e}")
            s3_key = ""

    documents[doc_id] = {
        "id": doc_id, "kind": "promesa_compraventa", "listing_id": req.listing_id,
        "buyer_id": req.buyer_id, "owner_id": listing.get("owner_id"),
        "amount_mxn": req.amount_mxn, "plazo_dias": req.plazo_dias,
        "pdf_path": pdf_path, "s3_key": s3_key,
        "sha256": sha, "nom151_token": timestamp_token,
        "status": "draft", "signers": {}, "created_at": datetime.utcnow().isoformat() + "Z",
    }
    return {"document_id": doc_id, "sha256": sha, "nom151_token": timestamp_token,
            "status": "draft", "amount_mxn": req.amount_mxn,
            "pdf_url": f"/api/documents/docs/{doc_id}/pdf",
            "storage": "s3" if s3_key else "local"}


@app.get("/docs/{doc_id}")
def get_doc(doc_id: str):
    d = documents.get(doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    return {k: v for k, v in d.items() if k != "pdf_path"}


@app.get("/docs/{doc_id}/pdf")
def get_doc_pdf(doc_id: str):
    d = documents.get(doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    # Prefer signed S3 URL when uploaded; otherwise stream the local file.
    if d.get("s3_key") and s3.enabled:
        return RedirectResponse(url=s3.signed_url(d["s3_key"]), status_code=307)
    return FileResponse(d["pdf_path"], media_type="application/pdf",
                        filename=f"{doc_id}-promesa.pdf")


@app.post("/docs/sign")
def sign(req: SignReq):
    d = documents.get(req.document_id)
    if not d:
        raise HTTPException(404, "doc not found")
    d["signers"][req.signer_role] = {
        "signer_id": req.signer_id,
        "signed_at": datetime.utcnow().isoformat() + "Z",
        "method": "Mifiel (mock NOM-151)",
        "signature_hash": hashlib.sha256(f"{req.signer_id}:{d['sha256']}".encode()).hexdigest(),
    }
    if {"buyer", "owner"}.issubset(d["signers"].keys()):
        d["status"] = "signed"
    else:
        d["status"] = "partially_signed"
    return d


@app.get("/docs")
def list_docs():
    return [{k: v for k, v in d.items() if k != "pdf_path"} for d in documents.values()]


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
    print(f"[documents] DSAR wiring deferred: {_e!r}")
    _DSAR_READY = False


# Phase 5.1 — Protocol-driven store wires the DSAR contract.
from store import (                                          # noqa: E402
    build_default_store as _build_documents_store,
)

_docs_store = _build_documents_store()


def set_documents_store(store) -> None:
    """Test hook — inject the in-memory store with pinned ids/time."""
    global _docs_store
    _docs_store = store


def get_documents_store():
    return _docs_store


async def _documents_dsar_gather(user_id: str, tenant_id: str) -> dict:
    return await _docs_store.gather_user_data(
        tenant_id=tenant_id, user_id=user_id,
    )


async def _documents_dsar_purge_v2(user_id: str, tenant_id: str) -> dict:
    return await _docs_store.purge_user_data(
        tenant_id=tenant_id, user_id=user_id,
    )


def _documents_dsar_purge_legacy(user_id: str, tenant_id: str) -> dict:
    """Per CFDI 4.0 art. 30 we MUST retain signed fiscal documents for
    5 years. Unsigned drafts can go; signed contracts get an anonymized
    redaction (buyer_id → 'redacted-by-dsar') so the legal hold survives
    without leaking PII back via the audit log."""
    drafts_dropped = 0
    signed_redacted = 0
    for doc_id, d in list(documents.items()):
        if d.get("buyer_id") != user_id:
            continue
        if (d.get("status") or "").lower() == "draft":
            documents.pop(doc_id, None)
            drafts_dropped += 1
        else:
            # Redact PII fields without dropping the record.
            d["buyer_id"] = "redacted-by-dsar"
            d["signers"] = {k: {"redacted": True} for k in (d.get("signers") or {})}
            signed_redacted += 1
    return {
        "count": drafts_dropped + signed_redacted,
        "deleted": True,
        "detail": {
            "drafts_dropped": drafts_dropped,
            "signed_redacted": signed_redacted,
            "retention_note": "signed records redacted, not deleted (CFDI 5y)",
        },
    }


if _DSAR_READY:
    app.add_middleware(AuthInjector)
    mount_dsar(
        app,
        gather=_documents_dsar_gather,
        purge=_documents_dsar_purge_v2,
        service_name="documents",
    )
