"""Recepcion de facturas electronicas de proveedores (XML v4.4): parsear, registrar, responder (aceptar/parcial/rechazar) y crear gasto."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Expense, ReceivedDocument
from ..services.documents import audit

router = APIRouter(prefix="/reception", tags=["recepcion"])


def _tag(xml: str, name: str) -> str | None:
    m = re.search(rf"<{name}(?:\s[^>]*)?>(.*?)</{name}>", xml, re.S)
    return m.group(1).strip() if m else None


def parse_xml(xml: str) -> dict:
    """Extrae lo esencial del comprobante v4.4 sin depender de esquemas (validacion fuerte la hace el proveedor fiscal)."""
    clave = _tag(xml, "Clave")
    if not clave or len(clave) != 50:
        raise HTTPException(422, "El XML no contiene una Clave de 50 dígitos")
    em = re.search(r"<Emisor>(.*?)</Emisor>", xml, re.S)
    emisor = em.group(1) if em else ""
    res = re.search(r"<ResumenFactura>(.*?)</ResumenFactura>", xml, re.S)
    resumen = res.group(1) if res else xml
    fecha = _tag(xml, "FechaEmision")
    return {
        "clave": clave,
        "consecutive": _tag(xml, "NumeroConsecutivo"),
        "issuer_name": _tag(emisor, "Nombre"),
        "issuer_id": _tag(emisor, "Numero"),
        "issue_date": date.fromisoformat(fecha[:10]) if fecha else None,
        "currency": _tag(resumen, "CodigoMoneda") or "CRC",
        "subtotal": Decimal(_tag(resumen, "TotalVentaNeta") or _tag(resumen, "TotalVenta") or "0"),
        "tax_total": Decimal(_tag(resumen, "TotalImpuesto") or "0"),
        "total": Decimal(_tag(resumen, "TotalComprobante") or "0"),
        "activity_code": _tag(xml, "CodigoActividadEmisor") or _tag(xml, "CodigoActividad"),
    }


def _out(r: ReceivedDocument):
    return {
        "id": r.id,
        "clave": r.clave,
        "consecutive": r.consecutive,
        "issuer_name": r.issuer_name,
        "issuer_id": r.issuer_id,
        "issue_date": r.issue_date,
        "currency": r.currency,
        "subtotal": r.subtotal,
        "tax_total": r.tax_total,
        "total": r.total,
        "iva_condition": r.iva_condition,
        "activity_code": r.activity_code,
        "action": r.action,
        "hacienda_status": r.hacienda_status,
        "expense_id": r.expense_id,
        "created_at": r.created_at,
        "has_response": bool(r.xml_response),
    }


@router.get("")
def inbox(p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    return [
        _out(r) for r in db.scalars(select(ReceivedDocument).where(ReceivedDocument.tenant_id == p.tenant.id).order_by(ReceivedDocument.id.desc()).limit(100))
    ]


@router.post("/upload", status_code=201)
async def upload(file: UploadFile = File(...), p: Principal = Depends(require("accounting", "crear")), db: Session = Depends(get_db)):
    raw = (await file.read(2 * 1024 * 1024)).decode("utf-8", errors="replace")
    status, r = receive_xml(db, p.tenant.id, raw)
    if status == "duplicado":
        raise HTTPException(409, "Ese comprobante ya fue recibido")
    if status == "respuesta":
        raise HTTPException(422, "Ese XML es una respuesta de Hacienda, no un comprobante: suba el XML de la factura")
    db.commit()
    return _out(r)


def receive_xml(db: Session, tenant_id: int, raw: str) -> tuple[str, ReceivedDocument | None]:
    """Registra un XML recibido (subida manual o bandeja IMAP). Devuelve ("nuevo"|"duplicado"|"respuesta", doc).
    Los MensajeHacienda que el proveedor adjunta (aceptacion de SU factura) no son comprobantes: se ignoran."""
    if "<MensajeHacienda" in raw[:2000]:
        return "respuesta", None
    info = parse_xml(raw)
    existing = db.scalar(select(ReceivedDocument).where(ReceivedDocument.tenant_id == tenant_id, ReceivedDocument.clave == info["clave"]))
    if existing:
        return "duplicado", existing
    r = ReceivedDocument(tenant_id=tenant_id, xml_document=raw, **info)
    db.add(r)
    db.flush()
    return "nuevo", r


class RespondIn(BaseModel):
    action: str = Field(pattern="^(aceptada|parcial|rechazada)$")
    iva_condition: str = Field("credito", pattern="^(credito|no_credito|proporcional)$")
    activity_code: str | None = None
    create_expense: bool = True
    category_id: int | None = None


@router.post("/{rid}/respond")
def respond(rid: int, data: RespondIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    r = db.get(ReceivedDocument, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Documento no encontrado")
    if r.action:
        raise HTTPException(409, f"Ya fue respondido como {r.action}")
    code = {"aceptada": "05", "parcial": "06", "rechazada": "07"}[data.action]
    r.action, r.iva_condition, r.activity_code = data.action, data.iva_condition, data.activity_code or r.activity_code
    # Mensaje receptor v4.4 (05 aceptado, 06 parcial, 07 rechazado). El proveedor fiscal lo firma y envia (Fase 2 real);
    # aqui se genera y se marca 'aprobado' con el sandbox.
    r.xml_response = (
        f'<?xml version="1.0" encoding="utf-8"?><MensajeReceptor><Clave>{r.clave}</Clave><NumeroCedulaEmisor>{r.issuer_id or ""}</NumeroCedulaEmisor>'
        f"<FechaEmisionDoc>{datetime.now(UTC).isoformat()}</FechaEmisionDoc><Mensaje>{code[1]}</Mensaje><CondicionImpuesto>{'01' if data.iva_condition == 'credito' else '02' if data.iva_condition == 'no_credito' else '04'}</CondicionImpuesto>"
        f"<MontoTotalImpuestoAcreditar>{r.tax_total if data.iva_condition != 'no_credito' else 0}</MontoTotalImpuestoAcreditar><MontoTotalDeGastoAplicable>{r.subtotal}</MontoTotalDeGastoAplicable>"
        f"<TotalFactura>{r.total}</TotalFactura><NumeroCedulaReceptor>{p.tenant.tax_id or ''}</NumeroCedulaReceptor><NumeroConsecutivoReceptor>{r.consecutive or ''}</NumeroConsecutivoReceptor></MensajeReceptor>"
    )
    r.hacienda_status = "aprobado" if ((p.tenant.settings or {}).get("einvoice_provider") == "sandbox") else "pendiente"
    if data.create_expense and data.action != "rechazada" and not r.expense_id:
        e = Expense(
            tenant_id=p.tenant.id,
            category_id=data.category_id,
            description=f"{r.issuer_name or 'Proveedor'} · {r.consecutive or r.clave[-10:]}",
            date=r.issue_date or date.today(),
            currency=r.currency,
            subtotal=r.subtotal,
            tax_rate=(Decimal(str(r.tax_total)) / Decimal(str(r.subtotal)) * 100).quantize(Decimal("0.001")) if Decimal(str(r.subtotal)) else 0,
            tax_amount=r.tax_total,
            total=r.total,
            iva_credit=data.iva_condition,
            reference=r.clave,
            received_clave=r.clave,
            created_by=p.user.id,
        )
        db.add(e)
        db.flush()
        r.expense_id = e.id
    audit(db, p.tenant.id, p.user.id, "reception", "received_document", r.id, {"action": data.action}, ip=p.ip)
    db.commit()
    return _out(r)


@router.get("/{rid}/xml/{which}")
def xml(rid: int, which: str, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    from fastapi import Response

    r = db.get(ReceivedDocument, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Documento no encontrado")
    body = r.xml_document if which == "document" else r.xml_response
    if not body:
        raise HTTPException(404, "XML no disponible")
    return Response(body, media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="{r.clave}-{which}.xml"'})
