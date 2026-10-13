"""Ingreso de webhooks (plan 5.3): se verifica firma, se guarda el evento crudo (idempotente) y se aplica el pago."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.crypto import decrypt
from ..core.db import get_db
from ..models import Invoice, PaymentGatewayConfig, PaymentLink, WebhookEvent
from ..providers.payments import OnvoAdapter
from ..schemas.sales import PaymentIn
from ..services.documents import add_payment

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/onvo/{tenant_id}")
async def onvo(tenant_id: int, request: Request, db: Session = Depends(get_db), signature: str | None = Header(None, alias="Onvo-Signature")):
    body = await request.body()
    cfg = db.scalar(
        select(PaymentGatewayConfig).where(PaymentGatewayConfig.tenant_id == tenant_id, PaymentGatewayConfig.provider == "onvo", PaymentGatewayConfig.active)
    )
    secret = decrypt(cfg.secret_encrypted) if cfg and cfg.secret_encrypted else None
    ok = OnvoAdapter.verify_signature(secret or "", signature, body)
    try:
        payload = await request.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, "JSON invalido") from e
    ext_id = str(payload.get("id") or payload.get("event_id") or "")
    if not ext_id:
        raise HTTPException(400, "Evento sin id")
    existing = db.scalar(select(WebhookEvent).where(WebhookEvent.provider == "onvo", WebhookEvent.external_id == ext_id, WebhookEvent.signature_ok))
    if existing:
        return {"ok": True, "duplicate": True}  # idempotente: siempre 200
    ev = WebhookEvent(
        provider="onvo", external_id=ext_id, event_type=str(payload.get("type", "")), signature_ok=ok, payload=payload, received_at=datetime.now(UTC)
    )
    db.add(ev)
    db.flush()
    if not ok:
        # se registra para auditoria sin consumir la clave de idempotencia (un reintento firmado debe aplicarse)
        ev.external_id = f"{ext_id}#invalid-{int(datetime.now(UTC).timestamp() * 1000)}"
        ev.result = "firma invalida: no se aplica"
        db.commit()
        raise HTTPException(401, "Firma invalida")
    ev.result = _apply(db, tenant_id, payload)
    ev.processed_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}


def _apply(db: Session, tenant_id: int, payload: dict) -> str:
    t = payload.get("type", "")
    data = payload.get("data") or {}
    meta = data.get("metadata") or {}
    if meta.get("order_id") and not meta.get("invoice_id"):
        return _apply_order(db, tenant_id, t, data, meta)
    inv_id = meta.get("invoice_id")
    if not inv_id:
        return "sin invoice_id en metadata"
    inv = db.get(Invoice, int(inv_id))
    if not inv or inv.tenant_id != tenant_id:
        return "factura no encontrada"
    amount = Decimal(str(data.get("amount", 0))) / Decimal(100)
    if t.endswith("succeeded"):
        p = add_payment(db, tenant_id, None, inv, PaymentIn(method="onvo", kind="captura", amount=amount, external_ref=str(data.get("id") or "")))
        p.provider, p.provider_event = "onvo", payload
        link = db.scalar(select(PaymentLink).where(PaymentLink.invoice_id == inv.id).order_by(PaymentLink.id.desc()))
        if link:
            link.paid_at = datetime.now(UTC)
        return f"pago {p.id} aplicado"
    if t.endswith("refunded"):
        p = add_payment(db, tenant_id, None, inv, PaymentIn(method="onvo", kind="reembolso", amount=amount, external_ref=str(data.get("id") or "")))
        p.provider, p.provider_event = "onvo", payload
        return f"reembolso {p.id} aplicado"
    return f"evento {t} ignorado"


def _apply_order(db: Session, tenant_id: int, t: str, data: dict, meta: dict) -> str:
    """Pedido de la tienda pagado con tarjeta. Idempotente: un pedido ya pagado no cambia; el monto debe cubrir el total."""
    from ..models import Order, Tenant
    from ..services import store_orders as so

    o = db.get(Order, int(meta["order_id"]))
    if not o or o.tenant_id != tenant_id or (meta.get("tenant_id") and int(meta["tenant_id"]) != tenant_id):
        return "pedido no encontrado"
    tenant = db.get(Tenant, tenant_id)
    amount = Decimal(str(data.get("amount", 0))) / Decimal(100)
    ref = str(data.get("id") or "")
    if t.endswith("succeeded"):
        if amount + Decimal("0.01") < Decimal(str(o.total)):
            so._note(o, "onvo", f"Pago ONVO por {amount} no cubre el total {o.total}: no se aplica")
            return f"monto {amount} menor al total del pedido {o.number}"
        if not so.mark_paid(db, tenant, o, by="onvo", ref=ref, note="Pago con tarjeta confirmado por ONVO"):
            return f"pedido {o.number} ya estaba pagado"
        so.notify_team(db, tenant, o, "pagado")
        return f"pedido {o.number} pagado"
    if t.endswith("refunded"):
        so._note(o, "onvo", f"ONVO reporta reembolso por {amount}")
        return f"reembolso registrado en historial de {o.number}"
    return f"evento {t} ignorado"
