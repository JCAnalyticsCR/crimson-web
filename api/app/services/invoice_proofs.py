"""Pago de una factura desde su enlace publico (/pagar): metodos con los datos de Mi Tienda -> Pagos y comprobantes.

Pedido 21/10 (item 16): la pagina mostraba tarjetas inertes (SINPE sin numero, "PayPal" que no hacia nada). Ahora:
- SINPE Movil y transferencia muestran los datos de Mi Tienda -> Pagos (numero, titular, banco, IBAN) y dejan subir el
  comprobante. El comprobante entra a la MISMA revision que los de pedidos web (Pedidos de la tienda -> Por atender) y
  al aprobarse registra el pago en la factura (documents.add_payment) y le manda el recibo al cliente. Rechazado: queda
  el motivo a la vista del cliente y la factura no cambia. Las facturas son de cada vendedor (services/ownership): el
  vendedor solo ve y aprueba los comprobantes de SUS facturas, y el aviso va a quien ve todas las facturas mas el
  vendedor de esa factura.
- Tarjeta solo si ONVO esta activo (llave en Ajustes -> Pasarelas o en el entorno): abre el Checkout de ONVO.
- Efectivo, si Mi Tienda lo tiene activo, solo informa (no lleva comprobante).
Nunca se aprueba solo: el comprobante es una foto que manda el cliente (la validacion automatica es el item 44, futuro).
"""

from __future__ import annotations

import html
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, or_, select
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


def _can_payments(db: Session, t: Tenant, role_code: str, *needs: tuple[str, str]) -> bool:
    from ..core.deps import _role_permissions, effective_permissions, perms_allow

    perms = effective_permissions(t, role_code, _role_permissions(db, role_code))
    return all(perms_allow(perms, m, a) for m, a in needs)


def notify_team(db: Session, t: Tenant, inv: Invoice, pr: InvoicePaymentProof) -> int:
    """El aviso del comprobante de un pedido web, pero las facturas son de cada vendedor (services/ownership): de los
    roles de Mi Tienda -> Pagos -> avisar a, solo los que ven TODAS las facturas (sales.ver_todo: admin,
    contabilidad...), mas el vendedor responsable de ESA factura (o quien la tecleo si no tiene vendedor) si puede ver
    cobros. Nunca a todo el rol ventas ni en copia a la lista de la empresa: lleva cliente, monto y referencia."""
    from ..models import TenantUser
    from .mail import member_inbox, queue_email

    e = html.escape
    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    monto = so.fmt_money(pr.amount if pr.amount is not None else inv.balance, inv.currency)
    titulo = f"Comprobante subido · factura {inv.number}"
    cuerpo = (
        f"<p><b>{e(titulo)}</b></p><p>{e(c.name if c else 'Cliente sin ficha')} · {e(LABEL.get(pr.method, pr.method))} · {e(monto)}"
        + (f" · ref. {e(pr.reference)}" if pr.reference else "")
        + "</p><p>Revíselo en Pedidos de la tienda → Por atender → Comprobantes de facturas. Al aprobarlo se registra el pago en la factura.</p>"
    )
    roles = [r for r in (so.checkout_cfg(t).get("notify_roles") or ("admin",)) if _can_payments(db, t, r, ("sales", "ver_todo"), ("payments", "ver"))]
    roles = roles or ["admin"]
    owner = inv.seller_id if inv.seller_id is not None else inv.created_by
    who = TenantUser.role_code.in_(roles)
    if owner:
        who = or_(who, TenantUser.user_id == owner)
    n, vistos = 0, set()
    for m in db.scalars(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.active, who)):
        if m.role_code not in roles and not _can_payments(db, t, m.role_code, ("payments", "ver")):
            continue  # el vendedor de la factura sin permiso de cobros: no lo puede revisar
        correo = member_inbox(m)  # su correo de avisos si el admin le puso uno (como el resto de los avisos internos)
        if not correo or correo in vistos or not m.user.active:
            continue
        vistos.add(correo)
        queue_email(db, t, correo, titulo, cuerpo, "invoice", inv.id, dedupe_key=f"comprobante:{pr.id}|{correo}", company_bcc=False)
        n += 1
    return n


def scope(p) -> list:
    """Alcance: con sales.ver_todo, todo; el vendedor, solo los comprobantes de SUS facturas (el mismo criterio que la
    lista de facturas y POST /invoices/{id}/payments, services/ownership)."""
    from .ownership import doc_mine

    if p is None or p.sees_all_sales:
        return []
    return [InvoicePaymentProof.invoice_id.in_(select(Invoice.id).where(Invoice.tenant_id == p.tenant.id, doc_mine(Invoice, p.user.id)))]


def pending_count(db: Session, tenant_id: int, p=None) -> int:
    """Comprobantes de facturas por revisar; con el Principal, solo los que el que mira puede ver."""
    return int(
        db.scalar(
            select(func.count(InvoicePaymentProof.id)).where(InvoicePaymentProof.tenant_id == tenant_id, InvoicePaymentProof.status == "pendiente", *scope(p))
        )
        or 0
    )


def reused_in(db: Session, pr: InvoicePaymentProof) -> list[str]:
    """El MISMO archivo (proof_media no lo duplica: mismo sha256 = mismo media_id) ya se uso en otra factura (en
    revision o aprobado) o en un pedido web: el comprobante de un pago no paga dos cosas. El revisor lo ve y tiene que
    confirmarlo para aprobar."""
    from ..models import Order

    out = [
        f"la factura {num}" + (" (aprobado)" if st == "aprobado" else " (en revisión)")
        for num, st in db.execute(
            select(Invoice.number, InvoicePaymentProof.status)
            .join(Invoice, Invoice.id == InvoicePaymentProof.invoice_id)
            .where(
                InvoicePaymentProof.tenant_id == pr.tenant_id,
                InvoicePaymentProof.media_id == pr.media_id,
                InvoicePaymentProof.id != pr.id,
                InvoicePaymentProof.status.in_(("pendiente", "aprobado")),
            )
        )
    ]
    for num, proofs in db.execute(select(Order.number, Order.proofs).where(Order.tenant_id == pr.tenant_id, Order.proofs.is_not(None))):
        if any(isinstance(x, dict) and x.get("media_id") == pr.media_id for x in proofs or []):
            out.append(f"el pedido web {num}")
    return out


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
        "reused_in": reused_in(db, pr) if pr.status == "pendiente" else [],
    }


def visible(db: Session, p, proof_id: int) -> InvoicePaymentProof:
    """El comprobante si el que mira puede ver su factura; lo ajeno (otro vendedor) no existe: 404, nunca 403."""
    from .ownership import owns_doc

    pr = db.get(InvoicePaymentProof, proof_id)
    inv = db.get(Invoice, pr.invoice_id) if pr is not None and pr.tenant_id == p.tenant.id else None
    if pr is None or inv is None or inv.tenant_id != p.tenant.id or not owns_doc(inv, p):
        raise HTTPException(404, "Comprobante no encontrado")
    return pr


def _pending(db: Session, tenant_id: int, proof_id: int, p=None) -> tuple[InvoicePaymentProof, Invoice]:
    """El comprobante y su factura BLOQUEADOS (como store_orders.lock_order): dos aprobaciones a la vez (doble clic,
    dos revisores en pestanas distintas) o dos comprobantes de la misma factura no registran dos pagos ni pasan del
    saldo: la segunda espera y ve el estado y el saldo ya actualizados. Lo ajeno (otro vendedor): 404."""
    from .ownership import owns_doc

    pr = db.get(InvoicePaymentProof, proof_id, with_for_update=True, populate_existing=True)
    if pr is None or pr.tenant_id != tenant_id:
        raise HTTPException(404, "Comprobante no encontrado")
    inv = db.get(Invoice, pr.invoice_id, with_for_update=True, populate_existing=True)
    if inv is None or inv.tenant_id != tenant_id or (p is not None and not owns_doc(inv, p)):
        raise HTTPException(404, "Comprobante no encontrado")
    if pr.status != "pendiente":
        raise HTTPException(409, f"Este comprobante ya fue {'aprobado' if pr.status == 'aprobado' else 'rechazado'}")
    return pr, inv


def approve(
    db: Session,
    t: Tenant,
    proof_id: int,
    user_id: int,
    *,
    amount: Decimal | None = None,
    paid_at: date | None = None,
    ip: str | None = None,
    p=None,
    confirm_reused: bool = False,
):
    """Aprobado: registra el pago en la factura (sin commit) y le manda el recibo al cliente (como el pago con ONVO).
    Monto: el que se indique al aprobar, si no el que dijo el cliente, si no el saldo; nunca mas que el saldo (lo de
    mas se arregla a mano). El mismo archivo ya usado en otra factura o en un pedido web pide confirmarlo."""
    from ..schemas.sales import PaymentIn
    from . import documents as docsvc

    pr, inv = _pending(db, t.id, proof_id, p)
    otros = reused_in(db, pr)
    if otros and not confirm_reused:
        raise HTTPException(409, f"Este mismo comprobante ya se subió en {', '.join(otros)}. Revisalo antes de aprobar y confirmá si es correcto.")
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
    det = {"proof_id": pr.id, "payment_id": pay.id, "monto": str(monto), **({"reusado_en": otros} if otros else {})}
    audit(db, t.id, user_id, "proof_approve", "invoice", inv.id, det, ip=ip)
    _receipt(db, t, inv, pr, monto)
    return pr, inv


def _receipt(db: Session, t: Tenant, inv: Invoice, pr: InvoicePaymentProof, amount: Decimal) -> None:
    """Lo que promete la pagina de pago ("te avisamos al aplicar el pago"): recibo al cliente con el saldo que queda,
    como el del pago con ONVO (si la ficha tiene correo)."""
    from .documents_send import queue_once
    from .render import money

    c = db.get(Customer, inv.customer_id) if inv.customer_id else None
    if c is None or c.tenant_id != t.id or not (c.email or "").strip():
        return
    e = html.escape
    saldo = _d(inv.balance)
    resto = f"Saldo pendiente: <b>{money(saldo, inv.currency)}</b>." if saldo > 0 else "La factura quedó pagada en su totalidad. ¡Gracias!"
    ref = f"Referencia: {e(pr.reference)}. " if pr.reference else ""
    body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto;color:#15131a">'
        f'<div style="border-bottom:3px solid #e2233a;padding:12px 0;font-size:18px;font-weight:700">{e(t.name)}</div>'
        f"<p>Revisamos su comprobante de {e(LABEL.get(pr.method, pr.method))} y aplicamos el pago de <b>{money(amount, inv.currency)}</b>"
        f" a la factura <b>{e(inv.number)}</b>.</p><p>{resto}</p>"
        f'<p style="color:#8a858f;font-size:12px">{ref}Cualquier consulta, responda a este correo.</p></div>'
    )
    queue_once(db, t, c.email.strip(), f"Pago aplicado · {inv.number} · {t.name}", body, "invoice", inv.id, dedupe_key=f"recibo-comprobante:{pr.id}")


def reject(db: Session, t: Tenant, proof_id: int, user_id: int, note: str, ip: str | None = None, p=None):
    """Rechazado (sin commit): el cliente ve el motivo en la pagina de pago y puede subir otro."""
    pr, inv = _pending(db, t.id, proof_id, p)
    motivo = (note or "").strip()
    if not motivo:
        raise HTTPException(422, "Indicá el motivo: el cliente lo ve en la página de pago")
    pr.status, pr.reviewed_by, pr.reviewed_at, pr.note = "rechazado", user_id, datetime.now(UTC), motivo[:300]
    audit(db, t.id, user_id, "proof_reject", "invoice", inv.id, {"proof_id": pr.id, "motivo": motivo[:300]}, ip=ip)
    return pr, inv
