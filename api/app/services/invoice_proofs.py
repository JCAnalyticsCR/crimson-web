"""Pago de una factura desde su enlace publico (/pagar): metodos con los datos de Mi Tienda -> Pagos y comprobantes.

Pedido 21/10 (item 16): la pagina mostraba tarjetas inertes (SINPE sin numero, "PayPal" que no hacia nada). Ahora:
- SINPE Movil y transferencia muestran los datos de Mi Tienda -> Pagos (numero, titular, banco, IBAN) y dejan subir el
  comprobante. El comprobante entra a la MISMA revision que los de pedidos web (Pedidos de la tienda -> Por atender,
  aviso a los mismos roles) y al aprobarse registra el pago en la factura (documents.add_payment). Rechazado: queda el
  motivo a la vista del cliente y la factura no cambia.
- Tarjeta solo si ONVO esta activo (llave en Ajustes -> Pasarelas o en el entorno): abre el Checkout de ONVO.
- Efectivo, si Mi Tienda lo tiene activo, solo informa (no lleva comprobante).
Nunca se aprueba solo: el comprobante es una foto que manda el cliente (la validacion automatica es el item 44, futuro).
"""

from __future__ import annotations

import html
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Customer, Invoice, InvoicePaymentProof, PaymentLink, Tenant
from . import onvo
from . import store_orders as so
from .documents import audit

PROOF_METHODS = ("sinpe", "transferencia")
MAX_PENDING = 5  # comprobantes sin revisar por factura (como los pedidos web)
LABEL = {"sinpe": "SINPE Móvil", "transferencia": "Transferencia bancaria", "tarjeta": "Tarjeta", "efectivo": "Efectivo"}
STATUS_LABEL = {"pendiente": "En revisión", "aprobado": "Aprobado", "rechazado": "Rechazado"}


def _d(v) -> Decimal:
    return Decimal(str(v if v is not None else 0))


def payable(inv: Invoice) -> bool:
    return inv.status != "anulada" and _d(inv.balance) > 0


def methods(db: Session, t: Tenant, inv: Invoice) -> list[dict]:
    """Los metodos de la pagina de pago, con los datos de Mi Tienda -> Pagos (store_orders.checkout_cfg; sin
    configurar hereda los metodos manuales de Ajustes). Solo lo que funciona: SINPE y transferencia con sus datos y
    comprobante, tarjeta si ONVO esta activo, efectivo informativo."""
    cfg = so.checkout_cfg(t)["methods"]
    saldo = so.fmt_money(inv.balance, inv.currency)
    out: list[dict] = []
    m = cfg.get("sinpe") or {}
    if m.get("active"):
        txt = (
            f"Enviá {saldo} por SINPE Móvil al {m['phone']}" + (f" a nombre de {m['holder']}" if m.get("holder") else "") + f". En el detalle escribí {inv.number}."
            if m.get("phone")
            else (m.get("note") or "Te enviamos el número de SINPE Móvil para pagar.")
        )
        out.append(
            {
                "code": "sinpe",
                "label": LABEL["sinpe"],
                "needs_proof": True,
                "instructions": txt,
                "details": [x for x in (("Número", m.get("phone")), ("A nombre de", m.get("holder"))) if x[1]],
                "note": m.get("note") if m.get("phone") else None,
            }
        )
    m = cfg.get("transferencia") or {}
    if m.get("active"):
        txt = (
            f"Transferí {saldo} a la cuenta IBAN {m['iban']}. En el detalle escribí {inv.number}."
            if m.get("iban")
            else (m.get("note") or "Te enviamos los datos de la cuenta para transferir.")
        )
        holder = (m.get("holder") or "") + (f" (céd. {m['holder_id']})" if m.get("holder") and m.get("holder_id") else "")
        out.append(
            {
                "code": "transferencia",
                "label": LABEL["transferencia"],
                "needs_proof": True,
                "instructions": txt,
                "details": [x for x in (("Banco", m.get("bank")), ("Cuenta IBAN", m.get("iban")), ("A nombre de", holder)) if x[1]],
                "note": m.get("note") if m.get("iban") else None,
            }
        )
    if onvo.secret_key(db, t.id) and payable(inv):
        out.append({"code": "tarjeta", "label": "Tarjeta (ONVO)", "needs_proof": False, "instructions": "Visa o Mastercard en la página segura de ONVO.", "details": [], "note": None})
    m = cfg.get("efectivo") or {}
    if m.get("active"):
        out.append({"code": "efectivo", "label": LABEL["efectivo"], "needs_proof": False, "instructions": m.get("note") or "Pago en efectivo.", "details": [], "note": None})
    return out


def proofs_of(db: Session, inv: Invoice) -> list[InvoicePaymentProof]:
    return list(db.scalars(select(InvoicePaymentProof).where(InvoicePaymentProof.invoice_id == inv.id).order_by(InvoicePaymentProof.id)))


def public_proofs(db: Session, inv: Invoice) -> list[dict]:
    """Lo que el cliente ve de sus comprobantes: estado y, si se rechazo, el motivo. Nunca quien reviso."""
    from ..models import Media

    out = []
    for pr in proofs_of(db, inv):
        m = db.get(Media, pr.media_id)
        out.append(
            {
                "id": pr.id,
                "method": LABEL.get(pr.method, pr.method),
                "filename": m.filename if m else "comprobante",
                "at": so.aware(pr.created_at),
                "status": pr.status,
                "status_label": STATUS_LABEL.get(pr.status, pr.status),
                "note": pr.note if pr.status == "rechazado" else None,
            }
        )
    return out


def upload(
    db: Session,
    t: Tenant,
    inv: Invoice,
    data: bytes,
    filename: str | None,
    method: str,
    *,
    reference: str | None = None,
    amount: Decimal | None = None,
    payer_name: str | None = None,
    ip: str | None = None,
) -> InvoicePaymentProof:
    """Comprobante del cliente (sin commit). Queda pendiente de revision y avisa al equipo."""
    if not payable(inv):
        raise HTTPException(409, "Esta factura no tiene saldo por pagar")
    avail = {m["code"] for m in methods(db, t, inv) if m["needs_proof"]}
    if method not in PROOF_METHODS or method not in avail:
        raise HTTPException(422, "Ese método de pago no recibe comprobantes")
    pend = db.scalar(select(func.count(InvoicePaymentProof.id)).where(InvoicePaymentProof.invoice_id == inv.id, InvoicePaymentProof.status == "pendiente")) or 0
    if pend >= MAX_PENDING:
        raise HTTPException(409, "Ya hay varios comprobantes en revisión para esta factura. Escribinos por WhatsApp.")
    if amount is not None and amount <= 0:
        raise HTTPException(422, "Monto inválido")
    m = so.proof_media(db, t, data, filename)
    if db.scalar(select(InvoicePaymentProof.id).where(InvoicePaymentProof.invoice_id == inv.id, InvoicePaymentProof.media_id == m.id)):
        raise HTTPException(409, "Ese comprobante ya estaba subido")
    pr = InvoicePaymentProof(
        tenant_id=t.id,
        invoice_id=inv.id,
        media_id=m.id,
        method=method,
        amount=amount,
        currency=inv.currency,
        reference=(reference or "").strip()[:120] or None,
        payer_name=(payer_name or "").strip()[:160] or None,
        status="pendiente",
    )
    db.add(pr)
    db.flush()
    audit(db, t.id, None, "proof", "invoice", inv.id, {"proof_id": pr.id, "media_id": m.id, "method": method}, ip=ip)
    notify_team(db, t, inv, pr)
    return pr


def notify_team(db: Session, t: Tenant, inv: Invoice, pr: InvoicePaymentProof) -> int:
    """Mismo aviso y mismos roles que el comprobante de un pedido web (Mi Tienda -> Pagos -> avisar a)."""
    from .mail import notify_roles

    e = html.escape
    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    monto = so.fmt_money(pr.amount if pr.amount is not None else inv.balance, inv.currency)
    titulo = f"Comprobante subido · factura {inv.number}"
    cuerpo = (
        f"<p><b>{e(titulo)}</b></p><p>{e(c.name if c else 'Cliente sin ficha')} · {e(LABEL.get(pr.method, pr.method))} · {e(monto)}"
        + (f" · ref. {e(pr.reference)}" if pr.reference else "")
        + "</p><p>Revisalo en Pedidos de la tienda → Por atender → Comprobantes de facturas. Al aprobarlo se registra el pago en la factura.</p>"
    )
    roles = tuple(so.checkout_cfg(t).get("notify_roles") or ("admin",))
    return notify_roles(db, t, roles, titulo, cuerpo, "invoice", inv.id)


def pending_count(db: Session, tenant_id: int) -> int:
    return int(db.scalar(select(func.count(InvoicePaymentProof.id)).where(InvoicePaymentProof.tenant_id == tenant_id, InvoicePaymentProof.status == "pendiente")) or 0)


def staff_out(db: Session, pr: InvoicePaymentProof) -> dict:
    from ..models import Media

    inv = db.get(Invoice, pr.invoice_id)
    c = db.get(Customer, inv.customer_id) if inv and inv.customer_id else None
    m = db.get(Media, pr.media_id)
    return {
        "id": pr.id,
        "invoice_id": pr.invoice_id,
        "invoice_number": inv.number if inv else None,
        "invoice_status": inv.status if inv else None,
        "customer_name": c.name if c else None,
        "currency": pr.currency,
        "balance": inv.balance if inv else None,
        "total": inv.total if inv else None,
        "method": pr.method,
        "method_label": LABEL.get(pr.method, pr.method),
        "amount": pr.amount,
        "reference": pr.reference,
        "payer_name": pr.payer_name,
        "filename": m.filename if m else None,
        "content_type": m.content_type if m else None,
        "status": pr.status,
        "status_label": STATUS_LABEL.get(pr.status, pr.status),
        "note": pr.note,
        "payment_id": pr.payment_id,
        "created_at": so.aware(pr.created_at),
        "reviewed_at": so.aware(pr.reviewed_at),
    }


def _pending(db: Session, tenant_id: int, proof_id: int) -> tuple[InvoicePaymentProof, Invoice]:
    pr = db.get(InvoicePaymentProof, proof_id)
    if pr is None or pr.tenant_id != tenant_id:
        raise HTTPException(404, "Comprobante no encontrado")
    if pr.status != "pendiente":
        raise HTTPException(409, f"Este comprobante ya fue {'aprobado' if pr.status == 'aprobado' else 'rechazado'}")
    inv = db.get(Invoice, pr.invoice_id)
    if inv is None or inv.tenant_id != tenant_id:
        raise HTTPException(404, "Factura no encontrada")
    return pr, inv


def approve(db: Session, t: Tenant, proof_id: int, user_id: int, *, amount: Decimal | None = None, paid_at: date | None = None, ip: str | None = None):
    """Aprobado: registra el pago en la factura (sin commit). Monto: el que se indique al aprobar, si no el que dijo el
    cliente, si no el saldo; nunca mas que el saldo (lo de mas se arregla a mano)."""
    from ..schemas.sales import PaymentIn
    from . import documents as docsvc

    pr, inv = _pending(db, t.id, proof_id)
    if inv.status == "anulada":
        raise HTTPException(409, "La factura está anulada: rechazá el comprobante")
    saldo = _d(inv.balance)
    if saldo <= 0:
        raise HTTPException(409, "La factura ya no tiene saldo: rechazá el comprobante si está repetido")
    monto = amount if amount is not None else (_d(pr.amount) if pr.amount is not None else saldo)
    if monto <= 0:
        raise HTTPException(422, "Monto inválido")
    if monto > saldo:
        raise HTTPException(422, f"El monto ({so.fmt_money(monto, inv.currency)}) es mayor que el saldo ({so.fmt_money(saldo, inv.currency)})")
    pay = docsvc.add_payment(
        db,
        t.id,
        user_id,
        inv,
        PaymentIn(
            method=pr.method,
            amount=monto,
            external_ref=pr.reference,
            paid_at=paid_at,
            notes=f"Comprobante subido por el cliente desde el enlace de pago (#{pr.id})",
        ),
    )
    pr.status, pr.reviewed_by, pr.reviewed_at, pr.payment_id = "aprobado", user_id, datetime.now(UTC), pay.id
    if _d(inv.balance) <= 0:
        for link in db.scalars(select(PaymentLink).where(PaymentLink.invoice_id == inv.id, PaymentLink.paid_at.is_(None))):
            link.paid_at = datetime.now(UTC)
    audit(db, t.id, user_id, "proof_approve", "invoice", inv.id, {"proof_id": pr.id, "payment_id": pay.id, "monto": str(monto)}, ip=ip)
    return pr, inv


def reject(db: Session, t: Tenant, proof_id: int, user_id: int, note: str, ip: str | None = None):
    """Rechazado (sin commit): el cliente ve el motivo en la pagina de pago y puede subir otro."""
    pr, inv = _pending(db, t.id, proof_id)
    motivo = (note or "").strip()
    if not motivo:
        raise HTTPException(422, "Indicá el motivo: el cliente lo ve en la página de pago")
    pr.status, pr.reviewed_by, pr.reviewed_at, pr.note = "rechazado", user_id, datetime.now(UTC), motivo[:300]
    audit(db, t.id, user_id, "proof_reject", "invoice", inv.id, {"proof_id": pr.id, "motivo": motivo[:300]}, ip=ip)
    return pr, inv
