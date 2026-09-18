"""Emision fiscal: arma el payload v4.4 desde la factura, llama al adapter, guarda XMLs y estados; NC para anular."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import Customer, EInvoiceDocument, Invoice, Tenant
from ..providers.einvoice import get_provider
from .documents import audit
from .sequences import next_number

ROOT = {
    "FE": "FacturaElectronica",
    "TE": "TiqueteElectronico",
    "FEE": "FacturaElectronicaExportacion",
    "NC": "NotaCreditoElectronica",
    "ND": "NotaDebitoElectronica",
}


def _payload(db: Session, inv: Invoice, tenant: Tenant, doc_type: str, consecutive: str, reference_clave: str | None = None) -> dict:
    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    if doc_type in ("FE", "FEE", "NC", "ND") and not (c and c.id_number):
        raise HTTPException(422, "La factura electronica requiere receptor con identificacion (use Tiquete si es consumidor final)")
    return {
        "root": ROOT[doc_type],
        "consecutive": consecutive,
        "currency": inv.currency,
        "fx": str(inv.fx_sell),
        "activity_code": inv.activity_code or ((tenant.settings or {}).get("activity_codes") or [""])[0],
        "sale_condition": inv.sale_condition,
        "payment_method": inv.payment_method,
        "emisor": {"name": tenant.legal_name or tenant.name, "tax_id": tenant.tax_id},
        "receptor": {"name": c.name, "id_type": c.id_type, "id_number": c.id_number, "email": c.email} if c else None,
        "lines": [
            {
                "name": ln.name,
                "cabys_code": ln.cabys_code,
                "unit": ln.unit,
                "quantity": str(ln.quantity),
                "unit_price": str(ln.unit_price),
                "subtotal": str(ln.subtotal),
                "tax_rate": str(ln.tax_rate),
                "tax_amount": str(ln.tax_amount),
                "total": str(ln.total),
            }
            for ln in inv.lines
        ],
        "subtotal": str(inv.subtotal),
        "discount_total": str(inv.discount_total),
        "tax_total": str(inv.tax_total),
        "total": str(inv.total),
        "reference_clave": reference_clave,
    }


def emit(db: Session, tenant: Tenant, user_id: int, inv: Invoice) -> EInvoiceDocument:
    if inv.status == "anulada":
        raise HTTPException(409, "Factura anulada")
    if inv.einvoice_status in ("pendiente", "aceptada"):
        raise HTTPException(409, f"La factura ya esta {inv.einvoice_status}")
    if not inv.lines:
        raise HTTPException(422, "La factura no tiene lineas")
    provider_name = (tenant.settings or {}).get("einvoice_provider") or settings.einvoice_provider
    prov = get_provider(provider_name)
    doc = EInvoiceDocument(
        tenant_id=tenant.id,
        invoice_id=inv.id,
        doc_type=inv.doc_type,
        consecutive=inv.consecutive or "",
        provider=prov.name,
        status="pendiente",
        attempts=1,
        sent_at=datetime.now(UTC),
    )
    db.add(doc)
    inv.einvoice_status = "pendiente"
    res = prov.emit(_payload(db, inv, tenant, inv.doc_type, inv.consecutive or ""))
    doc.provider_ref, doc.status, doc.clave, doc.xml_document, doc.xml_response, doc.hacienda_message = (
        res.provider_ref,
        res.status,
        res.clave,
        res.xml_document,
        res.xml_response,
        res.message,
    )
    if res.status in ("aceptada", "rechazada", "error"):
        doc.resolved_at = datetime.now(UTC)
    inv.einvoice_status = res.status if res.status != "error" else "sin_emitir"
    if res.status == "aceptada":
        inv.clave = res.clave
    audit(db, tenant.id, user_id, "emit", "invoice", inv.id, {"status": res.status, "clave": res.clave, "provider": prov.name})
    db.flush()
    if res.status == "error":
        raise HTTPException(502, res.message or "Error del proveedor fiscal")
    return doc


def credit_note(db: Session, tenant: Tenant, user_id: int, inv: Invoice, reason: str) -> EInvoiceDocument:
    """Anula una factura aceptada emitiendo Nota de Credito que referencia su clave."""
    if inv.einvoice_status != "aceptada" or not inv.clave:
        raise HTTPException(409, "Solo se anula con NC una factura aceptada por Hacienda")
    number, consecutive = next_number(db, tenant.id, "NC")
    prov = get_provider((tenant.settings or {}).get("einvoice_provider") or settings.einvoice_provider)
    doc = EInvoiceDocument(
        tenant_id=tenant.id,
        invoice_id=inv.id,
        doc_type="NC",
        consecutive=consecutive,
        reference_clave=inv.clave,
        provider=prov.name,
        status="pendiente",
        attempts=1,
        sent_at=datetime.now(UTC),
    )
    db.add(doc)
    res = prov.emit(_payload(db, inv, tenant, "NC", consecutive, inv.clave))
    doc.provider_ref, doc.status, doc.clave, doc.xml_document, doc.xml_response, doc.hacienda_message = (
        res.provider_ref,
        res.status,
        res.clave,
        res.xml_document,
        res.xml_response,
        res.message,
    )
    doc.resolved_at = datetime.now(UTC)
    if res.status != "aceptada":
        db.flush()
        raise HTTPException(502, res.message or "La NC no fue aceptada")
    inv.status = "anulada"
    audit(db, tenant.id, user_id, "void_nc", "invoice", inv.id, {"nc": number, "clave": res.clave, "reason": reason})
    db.flush()
    return doc


def documents_for(db: Session, inv: Invoice) -> list[EInvoiceDocument]:
    return db.scalars(select(EInvoiceDocument).where(EInvoiceDocument.invoice_id == inv.id).order_by(EInvoiceDocument.id)).all()
