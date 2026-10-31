"""Cartera con dueno (ventas etapa 2): el "Vendedor de la cuenta" de cada cliente y el traspaso de cartera.

Reglas:
- Customer.account_owner_id es el vendedor de la cuenta. Quien vende sin ver las ventas de todos (el vendedor,
  Principal.sees_all_customers) solo ve los clientes suyos en la lista y en los buscadores. La ficha de un cliente se
  abre ademas si tiene con el una cotizacion, factura u oportunidad (los enlaces de sus documentos nunca son un 404).
  Admin, jefe de ventas, contabilidad y el resto de los roles ven todos.
- El cliente que crea un vendedor queda suyo; el prospecto que se convierte (la oportunidad recibe su cliente o se
  cotiza) queda del responsable de la oportunidad, si el cliente no tenia dueno.
- Crear un cliente que ya existe (misma cedula, correo o telefono) y es de otro vendedor se bloquea: "Este cliente ya
  es atendido por <nombre>". Un cliente sin dueno lo toma un vendedor solo buscando su cedula o su correo exactos.
- El admin o el jefe de ventas (crm.cartera) cambian el dueno desde la ficha: bitacora y aviso al nuevo dueno.
- Traspaso de cartera de un clic: clientes, oportunidades abiertas y cotizaciones abiertas de un vendedor pasan a otro.
  Las comisiones ya devengadas o pendientes se quedan con el vendedor original: no se toca ninguna comision, las
  facturas no cambian de vendedor y una cotizacion que ya genero comision se queda con quien la vendio.
- Nada de esto cambia las reglas de comisiones.
"""

from __future__ import annotations

import html as htmllib
import re

from fastapi import HTTPException
from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session

from ..core.texto import plural
from ..models import Commission, Customer, Invoice, Opportunity, Quote, TenantUser, User
from .documents import audit

OPEN_QUOTE_STATES = ("creado", "enviada", "por_aprobar")
CLOSED_OPP_STATES = ("ganada", "perdida", "archivada")


# ---------- Visibilidad ----------
def customer_scope(p):
    """Filtro de lista y buscadores: todo, o solo la cartera propia."""
    return true() if p.sees_all_customers else Customer.account_owner_id == p.user.id


def _related(db: Session, p, cid: int) -> bool:
    """Tiene con el cliente una cotizacion, factura u oportunidad propia (asi el enlace de su documento abre la ficha)."""
    from .ownership import doc_mine

    uid, tid = p.user.id, p.tenant.id
    for model in (Quote, Invoice):
        if db.scalar(select(model.id).where(model.tenant_id == tid, model.customer_id == cid, doc_mine(model, uid)).limit(1)) is not None:
            return True
    return db.scalar(select(Opportunity.id).where(Opportunity.tenant_id == tid, Opportunity.customer_id == cid, Opportunity.owner_id == uid).limit(1)) is not None


def can_see(db: Session, p, c: Customer) -> bool:
    if p.sees_all_customers or c.account_owner_id == p.user.id:
        return True
    return _related(db, p, c.id)


# ---------- Duplicados ----------
def norm_id(v: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", v or "").upper()


def norm_phone(v: str | None) -> str:
    """Los ultimos 8 digitos (el numero de Costa Rica sin +506, guiones ni espacios)."""
    digits = re.sub(r"\D", "", v or "")
    return digits[-8:] if len(digits) >= 8 else ""


def find_duplicate(db: Session, tenant_id: int, id_number: str | None, email: str | None, phone: str | None = None) -> Customer | None:
    """Cliente activo con la misma cedula (sin guiones, espacios ni puntos), el mismo correo o el mismo telefono."""
    ced, correo, tel = norm_id(id_number), (email or "").strip().lower(), norm_phone(phone)
    conds = []
    if correo:
        conds.append(func.lower(func.trim(Customer.email)) == correo)
    if ced:
        guardada = func.upper(func.replace(func.replace(func.replace(func.trim(Customer.id_number), "-", ""), " ", ""), ".", ""))
        conds.append(guardada == ced)
    if conds:
        c = db.scalar(select(Customer).where(Customer.tenant_id == tenant_id, Customer.active, or_(*conds)).order_by(Customer.id).limit(1))
        if c is not None:
            return c
    if tel:
        # candidatos por los ultimos 4 digitos y comparacion exacta de los ultimos 8 en Python (los telefonos se
        # guardan con o sin guion, espacios o +506)
        like = f"%{tel[-4:]}%"
        for c in db.scalars(
            select(Customer).where(Customer.tenant_id == tenant_id, Customer.active, or_(Customer.phone.like(like), Customer.whatsapp.like(like))).order_by(Customer.id).limit(200)
        ):
            if tel in (norm_phone(c.phone), norm_phone(c.whatsapp)):
                return c
    return None


def exact_match(c: Customer, query: str | None) -> bool:
    """La busqueda es la cedula o el correo EXACTOS del cliente (asi se toma un cliente sin dueno)."""
    q = (query or "").strip()
    if not q:
        return False
    if "@" in q:
        return bool(c.email) and c.email.strip().lower() == q.lower()
    ced = norm_id(q)
    return len(ced) >= 9 and ced == norm_id(c.id_number)


def owner_name(db: Session, uid: int | None) -> str | None:
    u = db.get(User, uid) if uid else None
    return (u.full_name or u.email) if u else None


def busy_message(db: Session, c: Customer) -> str:
    return f"Este cliente ya es atendido por {owner_name(db, c.account_owner_id) or 'otro vendedor'}"


def guard_customer(db: Session, p, customer_id: int | None) -> None:
    """Un vendedor no arma documentos ni oportunidades para un cliente de otro vendedor."""
    if not customer_id or p.sees_all_customers:
        return
    c = db.get(Customer, customer_id)
    if c is not None and c.tenant_id == p.tenant.id and c.account_owner_id and c.account_owner_id != p.user.id:
        raise HTTPException(422, busy_message(db, c))


# ---------- Dueno ----------
def _member(db: Session, tenant_id: int, uid: int) -> TenantUser | None:
    return db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == uid))


def ensure_owner(db: Session, tenant_id: int, c: Customer | None, uid: int | None, actor_id: int | None, reason: str, ip: str | None = None) -> bool:
    """Le pone dueno a un cliente que no tiene (nunca cambia uno que ya tiene). uid tiene que poder vender."""
    from .ownership import eligible_seller

    if c is None or c.account_owner_id or not uid or not eligible_seller(db, tenant_id, uid):
        return False
    c.account_owner_id = uid
    audit(db, tenant_id, actor_id, "account_owner", "customer", c.id, before={"account_owner_id": None}, after={"account_owner_id": uid}, reason=reason, ip=ip)
    return True


def _notify(db: Session, tenant, uid: int, subject: str, body: str, entity: str, entity_id: int) -> None:
    from .mail import member_inbox, queue_email

    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.user_id == uid, TenantUser.active))
    correo = member_inbox(m) if m else None
    if correo:
        queue_email(db, tenant, correo, subject, body, entity, entity_id, company_bcc=False)


def set_owner(db: Session, p, c: Customer, new_uid: int | None, reason: str | None = None) -> Customer:
    """El admin o el jefe de ventas cambian el vendedor de la cuenta (None = sin vendedor). Bitacora y aviso."""
    from .email_layout import cta
    from .ownership import eligible_seller

    if new_uid is not None and not eligible_seller(db, p.tenant.id, new_uid):
        raise HTTPException(422, "Ese usuario no puede ser vendedor de la cuenta")
    antes = c.account_owner_id
    if antes == new_uid:
        return c
    c.account_owner_id = new_uid
    audit(
        db, p.tenant.id, p.user.id, "account_owner", "customer", c.id,
        before={"account_owner_id": antes, "vendedor": owner_name(db, antes)},
        after={"account_owner_id": new_uid, "vendedor": owner_name(db, new_uid)},
        reason=(reason or "").strip()[:300] or None, ip=p.ip,
    )
    if new_uid:
        e = htmllib.escape
        _notify(
            db, p.tenant, new_uid,
            f"Ahora usted atiende a {c.name} · {p.tenant.name}",
            f"<p>{e(p.user.full_name or 'Administración')} le asignó la cuenta de <b>{e(c.name)}</b>. Desde ahora usted es su vendedor.</p>"
            + cta(f"/clientes/{c.id}", "Abrir el cliente"),
            "customer", c.id,
        )
    return c


# ---------- Traspaso de cartera ----------
def _quotes_with_commission(db: Session, tenant_id: int):
    return select(Commission.quote_id).where(Commission.tenant_id == tenant_id, Commission.quote_id.is_not(None), Commission.status != "anulada")


def _portfolio(db: Session, tenant_id: int, uid: int) -> dict:
    clientes = db.scalars(select(Customer.id).where(Customer.tenant_id == tenant_id, Customer.account_owner_id == uid)).all()
    opps = db.scalars(
        select(Opportunity.id).where(Opportunity.tenant_id == tenant_id, Opportunity.owner_id == uid, Opportunity.status.not_in(CLOSED_OPP_STATES))
    ).all()
    abiertas = select(Quote.id).where(Quote.tenant_id == tenant_id, Quote.seller_id == uid, Quote.status.in_(OPEN_QUOTE_STATES))
    con_comision = set(db.scalars(abiertas.where(Quote.id.in_(_quotes_with_commission(db, tenant_id)))).all())
    cotis = [q for q in db.scalars(abiertas).all() if q not in con_comision]
    return {"clientes": list(clientes), "oportunidades": list(opps), "cotizaciones": cotis, "cotizaciones_con_comision": sorted(con_comision)}


def counts(db: Session, tenant_id: int, uid: int) -> dict:
    pf = _portfolio(db, tenant_id, uid)
    return {k: len(v) for k, v in pf.items()}


def has_portfolio(db: Session, tenant_id: int, uid: int) -> bool:
    c = counts(db, tenant_id, uid)
    return bool(c["clientes"] or c["oportunidades"] or c["cotizaciones"])


def preview(db: Session, p, from_uid: int) -> dict:
    from .store_orders import sales_team

    if _member(db, p.tenant.id, from_uid) is None:
        raise HTTPException(404, "Usuario no encontrado")
    return {
        "from": {"id": from_uid, "name": owner_name(db, from_uid)},
        "counts": counts(db, p.tenant.id, from_uid),
        # a quien se le puede pasar: el equipo comercial activo, sin el mismo vendedor
        "receivers": [x for x in sales_team(db, p.tenant.id) if x["id"] != from_uid],
    }


def transfer(db: Session, p, from_uid: int, to_uid: int) -> dict:
    """Pasa clientes, oportunidades abiertas y cotizaciones abiertas de from_uid a to_uid. No toca comisiones."""
    from .email_layout import cta
    from .ownership import eligible_seller

    tid = p.tenant.id
    if _member(db, tid, from_uid) is None:
        raise HTTPException(404, "Usuario no encontrado")
    if from_uid == to_uid:
        raise HTTPException(422, "Elija otro vendedor para recibir la cartera")
    if not eligible_seller(db, tid, to_uid):
        raise HTTPException(422, "Ese usuario no puede recibir la cartera (tiene que ser un vendedor activo)")
    pf = _portfolio(db, tid, from_uid)
    for c in db.scalars(select(Customer).where(Customer.id.in_(pf["clientes"] or [0]))):
        c.account_owner_id = to_uid
    for o in db.scalars(select(Opportunity).where(Opportunity.id.in_(pf["oportunidades"] or [0]))):
        o.owner_id = to_uid
    for q in db.scalars(select(Quote).where(Quote.id.in_(pf["cotizaciones"] or [0]))):
        q.seller_id = to_uid
    n = {k: len(v) for k, v in pf.items()}
    audit(
        db, tid, p.user.id, "portfolio_transfer", "user", from_uid,
        {
            "de": from_uid, "de_nombre": owner_name(db, from_uid), "a": to_uid, "a_nombre": owner_name(db, to_uid), **n,
            "ids": {k: v[:500] for k, v in pf.items()},
            "comisiones": "sin cambios: lo devengado o pendiente sigue con el vendedor original",
        },
        ip=p.ip,
    )
    e = htmllib.escape
    _notify(
        db, p.tenant, to_uid,
        f"Recibió la cartera de {owner_name(db, from_uid) or 'otro vendedor'} · {p.tenant.name}",
        f"<p>{e(p.user.full_name or 'Administración')} le traspasó la cartera de <b>{e(owner_name(db, from_uid) or 'otro vendedor')}</b>:</p>"
        f"<ul><li>{plural(n['clientes'], 'cliente', 'clientes')}</li>"
        f"<li>{plural(n['oportunidades'], 'oportunidad abierta', 'oportunidades abiertas')}</li>"
        f"<li>{plural(n['cotizaciones'], 'cotización abierta', 'cotizaciones abiertas')}</li></ul>"
        "<p>Desde ahora usted les da seguimiento.</p>" + cta("/clientes", "Abrir mis clientes"),
        "user", from_uid,
    )
    return {"ok": True, "counts": n, "to": {"id": to_uid, "name": owner_name(db, to_uid)}}
