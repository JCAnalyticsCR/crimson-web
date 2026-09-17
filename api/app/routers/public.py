"""Endpoints publicos (sin sesion): pagina de pago por enlace firmado."""

from __future__ import annotations

from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.security import read_purpose_token
from ..models import Customer, Invoice, PaymentLink, Tenant

router = APIRouter(prefix="/public", tags=["publico"])


@router.get("/pay/{token}")
def pay_page(token: str, db: Session = Depends(get_db)):
    try:
        payload = read_purpose_token("paylink", token)
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(410, "El enlace de pago expiro") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(404, "Enlace invalido") from e
    inv = db.get(Invoice, int(payload["inv"]))
    link = db.scalar(select(PaymentLink).where(PaymentLink.token == token))
    if not inv or not link or inv.tenant_id != int(payload["tid"]):
        raise HTTPException(404, "Enlace invalido")
    link.opened_count += 1
    link.opened_at = link.opened_at or datetime.now(UTC)
    db.commit()
    t = db.get(Tenant, inv.tenant_id)
    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    manual = (t.settings or {}).get("manual_payment_methods") or [
        {"name": "SINPE Movil", "instructions": "Envie el pago al numero indicado y comparta el comprobante."},
        {"name": "Transferencia", "instructions": "Cuenta IBAN indicada en la factura."},
    ]
    return {
        "business": {"name": t.name, "logo_url": t.logo_url},
        "invoice": {
            "number": inv.number,
            "date": inv.issue_date,
            "due_date": inv.due_date,
            "currency": inv.currency,
            "total": inv.total,
            "balance": inv.balance,
            "status": inv.status,
            "lines": [{"name": ln.name, "quantity": ln.quantity, "unit_price": ln.unit_price, "total": ln.total} for ln in inv.lines],
        },
        "customer": {"name": c.name} if c else None,
        "methods": {"online": {"onvo": False, "paypal": False}, "manual": manual},  # ONVO se activa en Fase 3
    }
