"""Personas de una empresa aliada que quedaron como usuarios internos (reunion 2, 21/10, pedido 20).

Un usuario de una empresa aliada vive SOLO en el portal de aliados (roles aliado / tecnico_aliado, filtrado "en
comun"). Como usuario interno veria el panel de Crimson entero. Esto lo detecta:
- por correo exacto (bloquea al invitar o editar): el correo es el de la empresa aliada o el de uno de sus contactos;
- por dominio (solo aviso en Ajustes -> Usuarios): el correo es del dominio de la empresa aliada (no los de correo
  gratuito conocidos ni el dominio de los administradores de Crimson). No bloquea: la lista de correos gratuitos nunca
  esta completa y un dominio compartido dejaria sin acceso a quien si debe tenerlo;
- por nombre (solo aviso): el nombre de la persona es el de la empresa aliada (una persona fisica dada de alta como
  aliado) o el de uno de sus contactos.
Nunca cambia datos: la accion "Pasar al portal de aliados" (routers/ally_admin.py) la hace una persona.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AllyCompany, TenantUser, User

FREE_MAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.es", "outlook.com", "outlook.es", "live.com", "msn.com",
    "yahoo.com", "yahoo.es", "icloud.com", "me.com", "proton.me", "protonmail.com", "aol.com", "racsa.co.cr", "ice.co.cr",
}  # fmt: skip


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def _domain(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    return e.rsplit("@", 1)[1] if "@" in e else None


@dataclass
class _Ally:
    company: AllyCompany
    emails: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)


def _index(db: Session, tenant_id: int) -> list[_Ally]:
    """Empresas aliadas activas con sus correos, dominios propios y nombres (empresa, razon social y contactos)."""
    admins = {
        _domain(e)
        for e in db.scalars(
            select(User.email).join(TenantUser, TenantUser.user_id == User.id).where(TenantUser.tenant_id == tenant_id, TenantUser.role_code == "admin")
        )
    }
    out = []
    for c in db.scalars(select(AllyCompany).where(AllyCompany.tenant_id == tenant_id, AllyCompany.active.is_(True))):
        a = _Ally(c)
        correos = [c.email, *((v or {}).get("email") for v in (c.contacts or {}).values() if isinstance(v, dict))]
        a.emails = {x.strip().lower() for x in correos if x and "@" in x}
        a.domains = {d for d in (_domain(x) for x in a.emails) if d and d not in FREE_MAIL and d not in admins}
        nombres = [c.name, c.legal_name, *((v or {}).get("name") for v in (c.contacts or {}).values() if isinstance(v, dict))]
        a.names = {_norm(x) for x in nombres if x and len(_norm(x)) >= 5}
        out.append(a)
    return out


def match_email(db: Session, tenant_id: int, email: str | None) -> AllyCompany | None:
    """La empresa aliada de la que ese correo es el de la empresa o el de un contacto (exacto), o None. Es lo que
    bloquea; el dominio solo avisa (matches)."""
    e = (email or "").strip().lower()
    if not e:
        return None
    hit = next((a for a in _index(db, tenant_id) if e in a.emails), None)
    return hit.company if hit else None


def matches(db: Session, tenant_id: int, users: list[User]) -> dict[int, dict]:
    """Para la lista de Ajustes -> Usuarios: {user_id: {company_id, company, reason}} de quien parece de un aliado.
    reason: "correo" (exacto: bloquea editar el rol o activarlo), "dominio" o "nombre" (solo aviso)."""
    idx = _index(db, tenant_id)
    if not idx:
        return {}
    out: dict[int, dict] = {}
    for u in users:
        e = (u.email or "").strip().lower()
        hit = next((a for a in idx if e in a.emails), None)
        reason = "correo"
        if hit is None:
            hit = next((a for a in idx if _domain(e) in a.domains), None)
            reason = "dominio"
        if hit is None:
            n = _norm(u.full_name)
            hit = next((a for a in idx if n and n in a.names), None)
            reason = "nombre"
        if hit is not None:
            out[u.id] = {"company_id": hit.company.id, "company": hit.company.name, "reason": reason}
    return out


def blocked_msg(c: AllyCompany) -> str:
    return (
        f"Ese correo es de la empresa aliada «{c.name}». Las personas de un aliado entran solo al portal de aliados: "
        f"invítela desde Administración → Aliados → {c.name} → Usuarios, o use «Pasar al portal de aliados»."
    )
