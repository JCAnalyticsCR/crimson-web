"""Endpoints publicos (sin sesion): pagina de pago por enlace firmado, con cobro en linea por ONVO.

El regreso desde ONVO (?onvo=volver) nunca confirma por si solo: /onvo/sync le pregunta a ONVO por la sesion que se
abrio desde este enlace y solo aplica el pago si ONVO dice que esta pagada (mismo camino que el webhook)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.ratelimit import public_limiter
from ..core.security import read_purpose_token
from ..models import Customer, Invoice, PaymentLink, Tenant
from ..services import onvo
from .auth import client_ip

router = APIRouter(prefix="/public", tags=["publico"])


def _link(db: Session, token: str) -> tuple[Tenant, Invoice, PaymentLink]:
    try:
        payload = read_purpose_token("paylink", token)
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(410, "El enlace de pago expiro") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(404, "Enlace invalido") from e
    inv = db.get(Invoice, int(payload["inv"]))
    link = db.scalar(select(PaymentLink).where(PaymentLink.token == token))
    if not inv or not link or inv.tenant_id != int(payload["tid"]) or link.invoice_id != inv.id:
        raise HTTPException(404, "Enlace invalido")
    return db.get(Tenant, inv.tenant_id), inv, link


@router.get("/pay/{token}")
def pay_page(token: str, db: Session = Depends(get_db)):
    t, inv, link = _link(db, token)
    link.opened_count += 1
    link.opened_at = link.opened_at or datetime.now(UTC)
    db.commit()
    return _pay_out(db, t, inv)


@router.post("/pay/{token}/onvo")
def pay_onvo(token: str, request: Request, db: Session = Depends(get_db)):
    """Abre la pagina segura de ONVO por el saldo pendiente de la factura."""
    public_limiter.hit(f"paylink-onvo:{client_ip(request)}")
    t, inv, link = _link(db, token)
    out = onvo.invoice_checkout(db, t, inv, link)
    db.commit()
    return out


@router.post("/pay/{token}/onvo/sync")
def pay_onvo_sync(token: str, request: Request, db: Session = Depends(get_db)):
    """Al volver de ONVO: confirma con ONVO la ultima sesion abierta desde este enlace y devuelve la pagina actualizada."""
    public_limiter.hit(f"paylink-sync:{client_ip(request)}")
    t, inv, link = _link(db, token)
    onvo.sync_invoice(db, t, inv, link)
    db.commit()
    db.refresh(inv)
    return _pay_out(db, t, inv)


def _pay_out(db: Session, t: Tenant, inv: Invoice) -> dict:
    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    online = bool(onvo.secret_key(db, t.id)) and inv.status != "anulada" and Decimal(str(inv.balance)) > 0
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
        "methods": {"online": {"onvo": online, "paypal": False}, "manual": manual},
    }
