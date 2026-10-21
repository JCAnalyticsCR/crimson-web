"""Cola fiscal "Por emitir" (plan maestro 1.7): lo que falta emitir mientras Hacienda no esta en produccion.

- GET /fiscal/queue: facturas por emitir (las mas viejas primero), el modo fiscal y "emitidas fuera este mes".
  Mismo alcance que la lista de facturas (sin sales.ver_todo, solo lo propio).
- POST /fiscal/queue/{id}/external {number, date}: "Marcar emitida en Fygaro". Permiso propio, sales.emitir_fuera
  (contabilidad, la duena de la emision, y el admin): la marca saca la venta de la cola y del correo de las 08:30, asi
  que una vendedora no la puede poner sobre lo suyo. Desde ahi emit() la rechaza con 409. Con produccion activa solo
  lo creado antes de activarla (services/fiscal_queue.py).
- DELETE /fiscal/queue/{id}/external: deshacer una marca puesta por error (solo quien configura la empresa).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Customer, Invoice
from ..services import fiscal_queue as fq
from ..services import hacienda_config as hc
from ..services.ownership import doc_scope, owns_doc

router = APIRouter(tags=["fiscal"])


class ExternalIn(BaseModel):
    number: str = Field(min_length=1, max_length=60)
    date: dt.date


def _invoice(db: Session, invoice_id: int, p: Principal) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    # lo ajeno no existe (404, como en la lista y el detalle de facturas)
    if not inv or inv.tenant_id != p.tenant.id or not owns_doc(inv, p):
        raise HTTPException(404, "Factura no encontrada")
    return inv


def _row(db: Session, inv: Invoice, today: dt.date, since: dt.datetime | None) -> dict:
    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    return {
        "id": inv.id,
        "number": inv.number,
        "doc_type": inv.doc_type,
        "customer_name": c.name if c else None,
        "currency": inv.currency,
        "total": inv.total,
        "balance": inv.balance,
        "status": inv.status,
        "issue_date": inv.issue_date,
        "due_date": inv.due_date,
        "seller_id": inv.seller_id,
        "days_waiting": max((today - inv.issue_date).days, 0) if inv.issue_date else 0,
        # sin_emitir, o aceptada/rechazada/error solo en simulado o en pruebas (sin validez fiscal)
        "einvoice_status": inv.einvoice_status,
        "created_at": inv.created_at,
        # se puede marcar en este modo (con produccion activa, solo lo creado antes de activarla)
        "markable": fq.markable_in_mode(inv, since),
    }


def _state(inv: Invoice) -> dict:
    return {
        "id": inv.id,
        "number": inv.number,
        "einvoice_status": inv.einvoice_status,
        "external_number": inv.external_number,
        "external_date": inv.external_date,
    }


@router.get("/fiscal/queue")
def queue(limit: int = Query(200, ge=1, le=500), p: Principal = Depends(require("sales", "ver")), db: Session = Depends(get_db)):
    today = fq.cr_today()
    scope = doc_scope(Invoice, p)
    rows = fq.queue_rows(db, p.tenant.id, scope, limit)
    since = fq.production_since(db, p.tenant)
    return {
        "mode": hc.hacienda_mode(db, p.tenant),
        "production_since": since,
        "today": today,
        "total": fq.queue_count(db, p.tenant.id, scope),
        "items": [_row(db, i, today, since) for i in rows],
        "oldest_issue_date": rows[0].issue_date if rows else None,
        "emitidas_fuera_mes": fq.external_month_count(db, p.tenant.id, today, scope),
        "mes": today.strftime("%Y-%m"),
        "can_mark": p.can("sales", "emitir_fuera"),
    }


@router.post("/fiscal/queue/{invoice_id}/external")
def mark_external(invoice_id: int, data: ExternalIn, p: Principal = Depends(require("sales", "emitir_fuera")), db: Session = Depends(get_db)):
    inv = _invoice(db, invoice_id, p)
    fq.mark_external(db, p.tenant, p.user.id, inv, data.number, data.date, ip=p.ip, visible=lambda other: owns_doc(other, p))
    db.commit()
    return _state(inv)


@router.delete("/fiscal/queue/{invoice_id}/external")
def undo_external(invoice_id: int, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    inv = _invoice(db, invoice_id, p)
    fq.undo_external(db, p.tenant, p.user.id, inv, ip=p.ip)
    db.commit()
    return _state(inv)
