"""Personas que se eligen en los selectores del panel ("Responsable" de oportunidades, vendedor de un pedido web,
tecnicos, agentes de soporte...). Reunion 2, pedido 26: en "Responsable" salian duplicados y usuarios inactivos.

Regla unica: solo membresias internas ACTIVAS de cuentas ACTIVAS (ni desactivadas, ni eliminadas/anonimizadas), una
fila por persona, ordenadas por nombre. Si dos personas activas se llaman igual, se distinguen por el correo (nunca
dos opciones identicas en una lista).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TenantUser, User


def active_members(db: Session, tenant_id: int, roles: Iterable[str] | None = None) -> list[TenantUser]:
    """Membresias internas activas (cuenta activa) de la empresa, opcionalmente de esos roles. Una por persona."""
    q = (
        select(TenantUser)
        .join(User, User.id == TenantUser.user_id)
        .where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.active,
            User.active,
            TenantUser.customer_id.is_(None),
            TenantUser.ally_company_id.is_(None),
        )
    )
    if roles is not None:
        q = q.where(TenantUser.role_code.in_(tuple(roles)))
    seen: set[int] = set()
    out: list[TenantUser] = []
    for m in db.scalars(q.order_by(User.full_name, User.id)):
        if m.user_id in seen:
            continue
        seen.add(m.user_id)
        out.append(m)
    return out


def labels(members: Iterable[TenantUser]) -> dict[int, str]:
    """Nombre visible de cada persona; si el nombre se repite entre personas distintas, con su correo."""
    ms = list(members)
    cuenta = Counter((m.user.full_name or "").strip().lower() for m in ms)
    out: dict[int, str] = {}
    for m in ms:
        name = (m.user.full_name or "").strip() or (m.user.email or "")
        out[m.user_id] = f"{name} · {m.user.email}" if cuenta[name.lower()] > 1 else name
    return out


def options(db: Session, tenant_id: int, roles: Iterable[str] | None = None) -> list[dict]:
    """[{id, name}] listo para un selector (sin inactivos ni duplicados), ordenado por nombre."""
    ms = active_members(db, tenant_id, roles)
    names = labels(ms)
    return sorted(({"id": m.user_id, "name": names[m.user_id]} for m in ms), key=lambda x: x["name"].lower())
