"""Revision de los comprobantes que suben los clientes desde la pagina de pago de la factura (/pagar).

Se ven en Pedidos de la tienda -> Por atender, junto a los comprobantes de pedidos web (misma revision). Aprobar
registra el pago en la factura; rechazar deja el motivo que el cliente ve en la pagina de pago. Ver
services/invoice_proofs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import InvoicePaymentProof, Media
from ..services import invoice_proofs as ip
from ..services.documents import audit

router = APIRouter(tags=["pagos"])


@router.get("/payment-proofs")
def list_proofs(
    status: str = Query("pendiente", pattern="^(pendiente|aprobado|rechazado|todos)$"),
    limit: int = Query(100, ge=1, le=300),
    p: Principal = Depends(require("payments", "ver")),
    db: Session = Depends(get_db),
):
    """El vendedor (sin sales.ver_todo) solo ve los comprobantes de SUS facturas (services/ownership)."""
    stmt = select(InvoicePaymentProof).where(InvoicePaymentProof.tenant_id == p.tenant.id, *ip.scope(p))
    if status != "todos":
        stmt = stmt.where(InvoicePaymentProof.status == status)
    rows = db.scalars(stmt.order_by(InvoicePaymentProof.id.desc()).limit(limit)).all()
    return {"items": [ip.staff_out(db, r) for r in rows], "pendientes": ip.pending_count(db, p.tenant.id, p)}


def _get(db: Session, p: Principal, pid: int) -> InvoicePaymentProof:
    return ip.visible(db, p, pid)


@router.get("/payment-proofs/{pid}/file")
def proof_file(pid: int, p: Principal = Depends(require("payments", "ver")), db: Session = Depends(get_db)):
    """El archivo privado, con las mismas cabeceras que el comprobante de un pedido web."""
    pr = _get(db, p, pid)
    m = db.get(Media, pr.media_id)
    if not m or m.tenant_id != p.tenant.id:
        raise HTTPException(404, "Comprobante no encontrado")
    audit(db, p.tenant.id, p.user.id, "view_proof", "invoice", pr.invoice_id, {"proof_id": pr.id}, ip=p.ip)
    db.commit()
    return Response(
        m.data,
        media_type=m.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="{m.filename}"',
            "Content-Security-Policy": "default-src 'none'; img-src 'self' data: blob:; style-src 'unsafe-inline'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal | None = Field(None, gt=0)
    paid_at: date | None = None
    # el mismo archivo ya se subio en otra factura o en un pedido web (staff_out.reused_in): aprobar pide confirmarlo
    confirm_reused: bool = False


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    note: str = Field(min_length=3, max_length=300)


@router.post("/payment-proofs/{pid}/approve")
def approve(pid: int, data: ApproveIn, p: Principal = Depends(require("payments", "crear")), db: Session = Depends(get_db)):
    pr, _inv = ip.approve(db, p.tenant, pid, p.user.id, amount=data.amount, paid_at=data.paid_at, ip=p.ip, p=p, confirm_reused=data.confirm_reused)
    db.commit()
    return ip.staff_out(db, pr)


@router.post("/payment-proofs/{pid}/reject")
def reject(pid: int, data: RejectIn, p: Principal = Depends(require("payments", "crear")), db: Session = Depends(get_db)):
    pr, _inv = ip.reject(db, p.tenant, pid, p.user.id, data.note, ip=p.ip, p=p)
    db.commit()
    return ip.staff_out(db, pr)
