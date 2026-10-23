"""Bandeja "Ventas por asignar" y "Cobros por confirmar".

Reglas clave:
- Venta por asignar: Invoice con seller_id IS NULL y house_sale IS NOT TRUE, estado no anulada,
  de cualquier origen (web, POS, admin, caja, recurrencia).
  Pedidos web entregados/pagados sin attended_by_id confirmado también entran (tab aparte en el mismo endpoint).
  Consultas (Opportunity sin owner_id, source="tienda web") entran como pestaña "Consultas".
- Sugerencia: quien cotizó/atendió al cliente en los últimos N días (configurable, default 90).
  Si no hay historial, el último vendedor de su factura más reciente.
- Asignar: Invoice.seller_id = vendedor, se devengan comisiones sobre pagos ya cobrados confirmados.
- Venta de la casa: Invoice.house_sale = True. Si ya tenía comisiones pendientes de devengar, no pasa nada
  (seller_id NULL => ya no había comisiones). Si se reasigna y se marca casa luego, las comisiones no pagadas
  se anulan.
- Reasignar: si el anterior ya tenía comisiones pagadas, genera ajuste negativo al anterior y positivo
  al nuevo (pendiente de aprobación).
- Cobros por confirmar: Payment.status="por_confirmar" — no baja saldo ni devenga comisión hasta confirmar.
  Confirmar: idem a agregar el pago hoy (llama recompute_balance + on_payment).
  Rechazar: pone rejection_reason y avisa al vendedor.
- Reparto de consultas web: "manual" (default) | "turno" | "solucion".
"""

from __future__ import annotations

import html as htmllib
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    AuditLog,
    Commission,
    Customer,
    Invoice,
    Opportunity,
    Payment,
    Quote,
    Tenant,
    User,
)
from .documents import audit, recompute_balance

# origen de las consultas de la tienda: "Sitio web" desde el 22/10 (antes "tienda web")
WEB_SOURCES = ("Sitio web", "tienda web")

# ---------- Configuración "Ventas" de la empresa ----------

VENTAS_DEFAULTS: dict = {
    "comision_asignada": "regla_vendedor",     # "regla_vendedor" | float (% propio)
    "clientes_nuevos_sin_atender": "por_asignar",   # "por_asignar" | "casa"
    "ventana_sugerencia_dias": 90,             # dias de historial para sugerir vendedor
    "asignacion_consultas_web": "manual",      # "manual" | "turno" | "solucion"
    "asignacion_consultas_web_solucion_map": {},  # {solution_code: user_id}
    "quienes_confirman_cobros": ["admin", "contabilidad"],  # roles con payments.confirmar
}


def ventas_settings(tenant) -> dict:
    raw = (getattr(tenant, "settings", None) or {}).get("ventas") or {}
    return {**VENTAS_DEFAULTS, **{k: v for k, v in raw.items() if k in VENTAS_DEFAULTS}}


# ---------- Sugerencia de vendedor ----------

def suggest_seller(db: Session, tenant_id: int, customer_id: int | None) -> int | None:
    """Quien atendió / cotizó antes a este cliente — el historial más reciente (últimos 90 días por defecto).
    Si no hay historial de cotizaciones, el seller_id de su factura más reciente."""
    if not customer_id:
        return None
    t = db.get(Tenant, tenant_id)
    dias = int(ventas_settings(t).get("ventana_sugerencia_dias") or 90)
    desde = datetime.now(UTC) - timedelta(days=dias)

    # 1. El vendedor de la cotización más reciente enviada o convertida de este cliente
    q_row = db.scalar(
        select(Quote.seller_id)
        .where(
            Quote.tenant_id == tenant_id,
            Quote.customer_id == customer_id,
            Quote.seller_id.is_not(None),
            Quote.created_at >= desde,
        )
        .order_by(Quote.id.desc())
        .limit(1)
    )
    if q_row:
        return q_row

    # 2. El seller_id de la última factura del cliente
    inv_row = db.scalar(
        select(Invoice.seller_id)
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.customer_id == customer_id,
            Invoice.seller_id.is_not(None),
            Invoice.status != "anulada",
        )
        .order_by(Invoice.id.desc())
        .limit(1)
    )
    return inv_row


# ---------- Bandeja "Ventas por asignar" ----------

def unassigned_invoices_count(db: Session, tenant_id: int) -> int:
    """Facturas sin vendedor y sin marcar como casa (para la tarjeta del inicio)."""
    return int(
        db.scalar(
            select(func.count(Invoice.id)).where(
                Invoice.tenant_id == tenant_id,
                Invoice.seller_id.is_(None),
                or_(Invoice.house_sale.is_(None), Invoice.house_sale == False),  # noqa: E712
                Invoice.status != "anulada",
            )
        )
        or 0
    )


def unassigned_invoices(db: Session, tenant_id: int) -> list[dict]:
    """Facturas sin vendedor para la bandeja."""
    from .store_orders import web_order_of_invoice

    rows = db.scalars(
        select(Invoice)
        .where(
            Invoice.tenant_id == tenant_id,
            Invoice.seller_id.is_(None),
            or_(Invoice.house_sale.is_(None), Invoice.house_sale == False),  # noqa: E712
            Invoice.status != "anulada",
        )
        .order_by(Invoice.id.desc())
        .limit(200)
    ).all()

    out = []
    for inv in rows:
        c = db.get(Customer, inv.customer_id) if inv.customer_id else None
        # origen
        is_web = web_order_of_invoice(db, inv.id, tenant_id) is not None
        origen = "web" if is_web else ("recurrencia" if inv.recurrence_id else "panel")
        sugerido = suggest_seller(db, tenant_id, inv.customer_id)
        # productos (resumen)
        items = [ln.name for ln in (inv.lines or [])[:3]]
        out.append({
            "id": inv.id,
            "number": inv.number,
            "customer_id": inv.customer_id,
            "customer_name": c.name if c else None,
            "total": inv.total,
            "balance": inv.balance,
            "currency": inv.currency,
            "status": inv.status,
            "issue_date": inv.issue_date,
            "origen": origen,
            "items": items,
            "suggested_seller_id": sugerido,
        })
    return out


def unassigned_opportunities_count(db: Session, tenant_id: int) -> int:
    """Oportunidades de la tienda web sin responsable (para la tarjeta del inicio)."""
    return int(
        db.scalar(
            select(func.count(Opportunity.id)).where(
                Opportunity.tenant_id == tenant_id,
                Opportunity.owner_id.is_(None),
                Opportunity.source.in_(WEB_SOURCES),
                Opportunity.status.not_in(("ganada", "perdida", "archivada")),
            )
        )
        or 0
    )


def unassigned_opportunities(db: Session, tenant_id: int) -> list[dict]:
    """Oportunidades (consultas web) sin responsable para la pestaña 'Consultas'."""
    rows = db.scalars(
        select(Opportunity)
        .where(
            Opportunity.tenant_id == tenant_id,
            Opportunity.owner_id.is_(None),
            Opportunity.source.in_(WEB_SOURCES),
            Opportunity.status.not_in(("ganada", "perdida", "archivada")),
        )
        .order_by(Opportunity.id.desc())
        .limit(200)
    ).all()
    out = []
    for o in rows:
        c = db.get(Customer, o.customer_id) if o.customer_id else None
        contact = o.contact or {}
        sugerido = suggest_seller(db, tenant_id, o.customer_id)
        out.append({
            "id": o.id,
            "number": o.number,
            "title": o.title,
            "customer_id": o.customer_id,
            "customer_name": c.name if c else contact.get("company") or contact.get("name"),
            "contact_name": contact.get("name"),
            "solution": o.solution,
            "amount": o.amount,
            "currency": o.currency,
            "status": o.status,
            "created_at": o.created_at,
            "antigüedad_dias": (datetime.now(UTC) - o.created_at.replace(tzinfo=UTC) if o.created_at and not o.created_at.tzinfo else (datetime.now(UTC) - o.created_at)).days if o.created_at else None,
            "suggested_seller_id": sugerido,
        })
    return out


# ---------- Asignar / marcar casa ----------

def _eligible(db: Session, tenant_id: int, seller_id: int) -> User:
    """El usuario puede ser vendedor: activo, del equipo, no cliente ni aliado. 404 si no cumple."""
    from .ownership import eligible_seller

    if not eligible_seller(db, tenant_id, seller_id):
        raise HTTPException(422, "El usuario no puede ser vendedor en esta empresa")
    u = db.get(User, seller_id)
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    return u


def _get_invoice(db: Session, tenant_id: int, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if not inv or inv.tenant_id != tenant_id:
        raise HTTPException(404, "Factura no encontrada")
    if inv.status == "anulada":
        raise HTTPException(409, "La factura está anulada")
    return inv


def assign_invoice_seller(
    db: Session,
    tenant: Tenant,
    invoice_id: int,
    seller_id: int,
    by_user_id: int,
    ip: str | None = None,
) -> Invoice:
    """Asigna un vendedor a una factura sin vendedor. Devenga las comisiones por pagos ya confirmados.
    Para reasignar (invoice ya tenía seller_id) usa reassign_invoice_seller."""
    from ..models import TenantUser
    from . import commissions as comsvc
    from .mail import member_inbox, queue_email

    inv = _get_invoice(db, tenant.id, invoice_id)
    if inv.seller_id is not None and inv.seller_id != seller_id:
        # reasignacion: otro path
        return reassign_invoice_seller(db, tenant, invoice_id, seller_id, by_user_id, ip)

    _eligible(db, tenant.id, seller_id)
    antes_seller = inv.seller_id
    antes_casa = inv.house_sale

    inv.seller_id = seller_id
    inv.house_sale = False

    audit(
        db, tenant.id, by_user_id, "assign_seller", "invoice", inv.id,
        before={"seller_id": antes_seller, "house_sale": antes_casa},
        after={"seller_id": seller_id, "house_sale": False},
        ip=ip,
    )
    db.flush()

    # Devengar comisiones por pagos confirmados que no las tienen todavía
    for pago in inv.payments:
        if pago.status == "confirmado" and pago.kind == "captura":
            comsvc.on_payment(db, pago, reassign=True)

    # Notificar al vendedor
    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.user_id == seller_id, TenantUser.active))
    correo = member_inbox(m) if m else None
    if correo:
        e = htmllib.escape
        asunto = f"Se le asignó la venta {e(inv.number)} · {tenant.name}"
        from .email_layout import cta
        cuerpo = (
            f"<p>Le asignaron la factura <b>{e(inv.number)}</b>"
            f"{(' · ' + e(db.get(Customer, inv.customer_id).name)) if inv.customer_id and db.get(Customer, inv.customer_id) else ''}.</p>"
            f"<p>Total: {inv.currency} {inv.total:,.0f}</p>"
            + cta(f"/facturas/{inv.id}", f"Abrir {e(inv.number)}")
        )
        queue_email(db, tenant, correo, asunto, cuerpo, "invoice", inv.id,
                    dedupe_key=f"assign-seller:{inv.id}:{seller_id}", company_bcc=False)
    return inv


def mark_invoice_house_sale(
    db: Session,
    tenant: Tenant,
    invoice_id: int,
    by_user_id: int,
    ip: str | None = None,
) -> Invoice:
    """Marca la factura como 'venta de la casa': no genera comisión para ningún vendedor.
    Si había comisiones pendientes (no pagadas) se anulan."""
    from . import commissions as comsvc

    inv = _get_invoice(db, tenant.id, invoice_id)
    antes_seller = inv.seller_id
    antes_casa = inv.house_sale

    # Anular comisiones pendientes (no pagadas)
    if inv.seller_id is not None:
        vivas = db.scalars(
            select(Commission).where(
                Commission.tenant_id == tenant.id,
                Commission.invoice_id == inv.id,
                Commission.status.in_(("en_espera", "pendiente", "aprobada")),
            )
        ).all()
        comsvc.reverse_or_void(db, vivas, "Venta de la casa", by_user_id)

    inv.seller_id = None
    inv.house_sale = True

    audit(
        db, tenant.id, by_user_id, "house_sale", "invoice", inv.id,
        before={"seller_id": antes_seller, "house_sale": antes_casa},
        after={"seller_id": None, "house_sale": True},
        ip=ip,
    )
    return inv


def reassign_invoice_seller(
    db: Session,
    tenant: Tenant,
    invoice_id: int,
    new_seller_id: int,
    by_user_id: int,
    ip: str | None = None,
) -> Invoice:
    """Reasigna el vendedor de una factura que ya tenía uno.
    - Comisiones no pagadas del anterior: se anulan.
    - Comisiones pagadas del anterior: ajuste negativo pendiente de aprobación.
    - Comisiones del nuevo: se devengan sobre pagos ya confirmados."""
    from . import commissions as comsvc
    from .mail import member_inbox, queue_email

    inv = _get_invoice(db, tenant.id, invoice_id)
    _eligible(db, tenant.id, new_seller_id)

    prev_seller = inv.seller_id
    if prev_seller == new_seller_id:
        raise HTTPException(422, "El vendedor ya es el mismo")

    antes = {"seller_id": prev_seller}

    # 1. Reversar/anular comisiones del vendedor anterior
    vivas = db.scalars(
        select(Commission).where(
            Commission.tenant_id == tenant.id,
            Commission.invoice_id == inv.id,
            Commission.beneficiary == "vendedor",
            Commission.status != "anulada",
        )
    ).all()
    if vivas:
        comsvc.reverse_or_void(db, vivas, f"Reasignación a {new_seller_id}", by_user_id)

    inv.seller_id = new_seller_id
    inv.house_sale = False

    audit(
        db, tenant.id, by_user_id, "reassign_seller", "invoice", inv.id,
        before=antes,
        after={"seller_id": new_seller_id},
        ip=ip,
    )
    db.flush()

    # 2. Devengar comisiones del nuevo vendedor sobre pagos ya confirmados
    for pago in inv.payments:
        if pago.status == "confirmado" and pago.kind == "captura":
            comsvc.on_payment(db, pago, reassign=True)

    # 3. Notificar al nuevo vendedor
    from ..models import TenantUser
    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.user_id == new_seller_id, TenantUser.active))
    correo = member_inbox(m) if m else None
    if correo:
        e = htmllib.escape
        from .email_layout import cta
        c_name = ""
        if inv.customer_id:
            cust = db.get(Customer, inv.customer_id)
            c_name = f" · {e(cust.name)}" if cust else ""
        queue_email(
            db, tenant, correo,
            f"Se le asignó la venta {e(inv.number)} · {tenant.name}",
            f"<p>Le asignaron la factura <b>{e(inv.number)}</b>{c_name}.</p>"
            f"<p>Total: {inv.currency} {inv.total:,.0f}</p>"
            + cta(f"/facturas/{inv.id}", f"Abrir {e(inv.number)}"),
            "invoice", inv.id,
            dedupe_key=f"reassign-seller:{inv.id}:{new_seller_id}",
            company_bcc=False,
        )
    return inv


# ---------- Asignar responsable a consulta web (Opportunity) ----------

def assign_opportunity_owner(
    db: Session,
    tenant: Tenant,
    opp_id: int,
    owner_id: int,
    by_user_id: int,
    ip: str | None = None,
) -> Opportunity:
    """Asigna responsable a una consulta web sin dueño. Notifica al vendedor con enlace pero SIN datos del prospecto."""
    from ..models import TenantUser
    from .email_layout import cta
    from .mail import member_inbox, queue_email

    o = db.get(Opportunity, opp_id)
    if not o or o.tenant_id != tenant.id:
        raise HTTPException(404, "Consulta no encontrada")
    _eligible(db, tenant.id, owner_id)

    antes = o.owner_id
    o.owner_id = owner_id
    audit(db, tenant.id, by_user_id, "assign_owner", "opportunity", o.id,
          before={"owner_id": antes}, after={"owner_id": owner_id}, ip=ip)
    db.flush()

    # Notificar al vendedor SIN datos del prospecto (número de la consulta + enlace)
    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.user_id == owner_id, TenantUser.active))
    correo = member_inbox(m) if m else None
    if correo:
        e = htmllib.escape
        queue_email(
            db, tenant, correo,
            f"Nueva consulta asignada: {e(o.number)} · {tenant.name}",
            f"<p>Se le asignó la oportunidad <b>{e(o.number)}</b>.</p>"
            + cta(f"/oportunidades?id={o.id}", f"Abrir {e(o.number)}"),
            "opportunity", o.id,
            dedupe_key=f"assign-opp:{o.id}:{owner_id}",
            company_bcc=False,
        )
    return o


# ---------- Reparto automático de consultas web ----------

def auto_assign_opportunity(db: Session, tenant: Tenant, opp: Opportunity) -> int | None:
    """Según la configuración 'asignacion_consultas_web', asigna el responsable al crear la oportunidad.
    Devuelve el user_id asignado (o None si el modo es 'manual')."""
    from .store_orders import sales_team

    cfg = ventas_settings(tenant)
    modo = cfg.get("asignacion_consultas_web", "manual")
    if modo == "manual":
        return None

    equipo = sales_team(db, tenant.id)
    if not equipo:
        return None

    if modo == "solucion" and opp.solution:
        mapa = cfg.get("asignacion_consultas_web_solucion_map") or {}
        uid = mapa.get(opp.solution)
        if uid and any(u["id"] == uid for u in equipo):
            return uid

    # turno (o solucion sin match): round-robin sobre el equipo activo
    uids = [u["id"] for u in equipo]
    if not uids:
        return None
    # Quién fue el último asignado en esta empresa (bitácora)
    ultimo = db.scalar(
        select(AuditLog.diff)
        .where(
            AuditLog.tenant_id == tenant.id,
            AuditLog.action == "auto_assign_opp",
            AuditLog.entity == "opportunity",
        )
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    last_uid = (ultimo or {}).get("owner_id") if isinstance(ultimo, dict) else None
    try:
        idx = uids.index(last_uid)
        next_uid = uids[(idx + 1) % len(uids)]
    except (ValueError, IndexError):
        next_uid = uids[0]

    opp.owner_id = next_uid
    audit(db, tenant.id, None, "auto_assign_opp", "opportunity", opp.id, {"owner_id": next_uid, "mode": modo})
    return next_uid


# ---------- Cobros por confirmar ----------

def pending_confirmation_count(db: Session, tenant_id: int) -> int:
    """Cuántos pagos están en 'por_confirmar' (para tarjeta del inicio)."""
    return int(
        db.scalar(
            select(func.count(Payment.id)).where(
                Payment.tenant_id == tenant_id,
                Payment.status == "por_confirmar",
            )
        )
        or 0
    )


def pending_confirmation_list(db: Session, tenant_id: int) -> list[dict]:
    """Lista de cobros por confirmar."""
    rows = db.scalars(
        select(Payment)
        .where(Payment.tenant_id == tenant_id, Payment.status == "por_confirmar")
        .order_by(Payment.id.desc())
        .limit(200)
    ).all()
    out = []
    for p in rows:
        inv = db.get(Invoice, p.invoice_id) if p.invoice_id else None
        created_by_user = db.get(User, p.created_by) if p.created_by else None
        c = db.get(Customer, inv.customer_id) if inv and inv.customer_id else None
        out.append({
            "id": p.id,
            "invoice_id": p.invoice_id,
            "invoice_number": inv.number if inv else None,
            "customer_name": c.name if c else None,
            "method": p.method,
            "kind": p.kind,
            "amount": p.amount,
            "currency": p.currency,
            "paid_at": p.paid_at,
            "notes": p.notes,
            "external_ref": p.external_ref,
            "created_by_id": p.created_by,
            "created_by_name": created_by_user.full_name if created_by_user else None,
        })
    return out


def confirm_payment(
    db: Session,
    tenant: Tenant,
    invoice_id: int,
    payment_id: int,
    by_user_id: int,
    ip: str | None = None,
) -> Payment:
    """Confirma un cobro 'por_confirmar': baja saldo, devenga comisión."""
    from . import commissions as comsvc

    inv = _get_invoice(db, tenant.id, invoice_id)
    pago = db.get(Payment, payment_id)
    if not pago or pago.invoice_id != invoice_id or pago.tenant_id != tenant.id:
        raise HTTPException(404, "Cobro no encontrado")
    if pago.status != "por_confirmar":
        raise HTTPException(409, f"El cobro no está pendiente de confirmación (estado: {pago.status})")

    pago.status = "confirmado"
    pago.confirmed_by = by_user_id
    pago.confirmed_at = datetime.now(UTC)
    pago.rejection_reason = None

    # Ahora sí recalcula saldo y devenga comisión
    recompute_balance(inv)
    db.flush()
    comsvc.on_payment(db, pago)

    audit(db, tenant.id, by_user_id, "confirm_payment", "invoice", inv.id,
          {"payment_id": pago.id, "amount": str(pago.amount), "method": pago.method}, ip=ip)
    return pago


def reject_payment(
    db: Session,
    tenant: Tenant,
    invoice_id: int,
    payment_id: int,
    motivo: str,
    by_user_id: int,
    ip: str | None = None,
) -> Payment:
    """Rechaza un cobro 'por_confirmar': lo marca fallido, avisa al vendedor."""
    from ..models import TenantUser
    from .mail import member_inbox, queue_email

    inv = _get_invoice(db, tenant.id, invoice_id)
    pago = db.get(Payment, payment_id)
    if not pago or pago.invoice_id != invoice_id or pago.tenant_id != tenant.id:
        raise HTTPException(404, "Cobro no encontrado")
    if pago.status != "por_confirmar":
        raise HTTPException(409, f"El cobro no está pendiente de confirmación (estado: {pago.status})")

    motivo = (motivo or "").strip()
    if not motivo:
        raise HTTPException(422, "Indicá el motivo del rechazo")

    pago.status = "fallido"
    pago.rejection_reason = motivo[:300]
    pago.confirmed_by = by_user_id
    pago.confirmed_at = datetime.now(UTC)

    audit(db, tenant.id, by_user_id, "reject_payment", "invoice", inv.id,
          {"payment_id": pago.id, "motivo": motivo[:300]}, ip=ip)

    # Notificar al vendedor que registró el cobro
    if pago.created_by:
        m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.user_id == pago.created_by, TenantUser.active))
        correo = member_inbox(m) if m else None
        if correo:
            e = htmllib.escape
            from .email_layout import cta
            queue_email(
                db, tenant, correo,
                f"Cobro rechazado · {inv.number} · {tenant.name}",
                f"<p>El cobro de <b>{pago.currency} {pago.amount:,.0f}</b> ({e(pago.method)}) "
                f"registrado en la factura <b>{e(inv.number)}</b> fue rechazado.</p>"
                f"<p><b>Motivo:</b> {e(motivo)}</p>"
                + cta(f"/facturas/{inv.id}", f"Ver {e(inv.number)}"),
                "invoice", inv.id,
                dedupe_key=f"reject-pay:{pago.id}",
                company_bcc=False,
            )
    return pago
