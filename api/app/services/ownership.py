"""Alcance comercial del vendedor: que cotizaciones, facturas y cobros son "suyos".

Una sola definicion para listas, detalle, buscadores, conteos del inicio, ficha del cliente y PDF: asi un numero y
una lista nunca dicen cosas distintas, y lo ajeno no aparece en ningun lado ("que ella no lo sepa", 13/10 35:50).

"Lo mio" = soy el vendedor responsable (seller_id), o el documento no tiene vendedor y lo tecleé yo (created_by),
p. ej. la factura de un pedido de la tienda que facturo la vendedora. created_by nunca es el dueno por si solo: la
cotizacion que la oficina genera desde el levantamiento es de la vendedora de la oportunidad, no de quien la costeo.
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select, true

from ..models import Invoice, Payment


def doc_mine(model, user_id: int):
    """Soy el vendedor responsable, o el documento no tiene vendedor y lo tecleé yo."""
    return or_(model.seller_id == user_id, and_(model.seller_id.is_(None), model.created_by == user_id))


def doc_scope(model, p):
    """Filtro de lista: todo con sales.ver_todo; si no, lo mio."""
    return true() if p.sees_all_sales else doc_mine(model, p.user.id)


def owns_doc(obj, p) -> bool:
    """El mismo criterio que doc_scope para un documento suelto (detalle, PDF, correo, acciones)."""
    if p.sees_all_sales:
        return True
    uid = p.user.id
    return obj.seller_id == uid or (obj.seller_id is None and obj.created_by == uid)


def payment_scope(p):
    """Mis cobros: los que registre y los aplicados a mis facturas (aunque los haya registrado caja)."""
    if p.sees_all_sales:
        return true()
    mias = select(Invoice.id).where(Invoice.tenant_id == p.tenant.id, doc_mine(Invoice, p.user.id))
    return or_(Payment.created_by == p.user.id, Payment.invoice_id.in_(mias))


def visible_invoice_ids(db, p, ids) -> set[int]:
    """De estas facturas, cuales son del que mira (pedidos con su factura): una consulta para toda la lista. Al
    vendedor la factura que hizo otro le sale como numero sin enlace (abrirla seria un 404)."""
    ids = {i for i in ids if i}
    if not ids:
        return set()
    if p.sees_all_sales:
        return ids
    return set(db.scalars(select(Invoice.id).where(Invoice.tenant_id == p.tenant.id, Invoice.id.in_(ids), doc_mine(Invoice, p.user.id))))


def eligible_seller(db, tenant_id: int, uid: int | None) -> bool:
    """Puede ser el vendedor de un documento (verlo y comisionarlo): usuario ACTIVO del equipo de esta empresa, ni
    de un cliente ni de un aliado. Vale igual para elegirlo en el editor que para heredarlo de la oportunidad (cuyo
    responsable solo se valida como miembro activo, pipeline._check_owner)."""
    from ..core.deps import ALLY_USER_ROLES, CLIENT_ROLES
    from ..models import TenantUser

    if not uid:
        return False
    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == uid, TenantUser.active))
    return bool(
        m and m.customer_id is None and m.ally_company_id is None and m.role_code not in CLIENT_ROLES and m.role_code not in ALLY_USER_ROLES and m.user.active
    )


def opportunity_seller(db, o) -> int | None:
    """El vendedor que hereda la cotizacion de una oportunidad: su responsable, si puede ser vendedor. Si no (sin
    responsable, desactivado, de un cliente o de un aliado), None: quien llama decide (quien teclea o lo que ya habia)."""
    return o.owner_id if o is not None and eligible_seller(db, o.tenant_id, o.owner_id) else None
