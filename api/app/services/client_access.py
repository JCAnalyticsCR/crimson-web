"""Piezas compartidas del acceso de clientes: invitaciones al portal y sugerencias de cliente para una solicitud."""

from __future__ import annotations

import html
import re
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.deps import CLIENT_ROLES
from ..core.security import hash_token
from ..models import AccessRequest, Customer, CustomerContact, Invitation, Tenant, TenantUser, User
from .mail import queue_email

INVITE_DAYS = 7
ROLE_LABEL = {"cliente_admin": "Administrador de su empresa", "cliente_usuario": "Usuario"}


def digitos(s: str | None, ultimos: int | None = None) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-ultimos:] if ultimos else d


def invite_client(db: Session, tenant: Tenant, customer: Customer, email: str, role: str, invited_by: int | None, name: str | None = None) -> dict:
    """Crea la invitacion al portal ligada a ESE cliente y la manda por correo si se puede.
    Devuelve {id, link, emailed}: sin Resend el correo queda simulado y la pantalla ofrece el enlace para
    mandarlo por WhatsApp, sin afirmar que llego."""
    if role not in CLIENT_ROLES:
        raise HTTPException(422, "Rol de cliente inválido")
    if customer.tenant_id != tenant.id:
        raise HTTPException(404, "Cliente no encontrado")
    email = email.strip().lower()
    u = db.scalar(select(User).where(User.email == email))
    if u:
        m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.user_id == u.id))
        # una cuenta interna (o de OTRO cliente) nunca se convierte en usuario de este cliente
        if m and m.customer_id != customer.id:
            raise HTTPException(409, "Ese correo ya tiene otro acceso en la empresa. Use otro correo.")
        if m and m.active:
            raise HTTPException(409, "Esa persona ya tiene acceso al portal de este cliente.")
    ahora = datetime.now(UTC)
    # una sola invitacion viva por correo y cliente: la anterior deja de servir
    for old in db.scalars(
        select(Invitation).where(
            Invitation.tenant_id == tenant.id, func.lower(Invitation.email) == email, Invitation.accepted_at.is_(None), Invitation.expires_at > ahora
        )
    ):
        old.expires_at = ahora
    raw = secrets.token_urlsafe(32)
    inv = Invitation(
        tenant_id=tenant.id,
        email=email,
        role_code=role,
        token_hash=hash_token(raw),
        expires_at=ahora + timedelta(days=INVITE_DAYS),
        invited_by=invited_by,
        customer_id=customer.id,
    )
    db.add(inv)
    db.flush()
    link = f"{settings.public_base_url.rstrip('/')}/invitacion/{raw}"
    e = html.escape
    m = queue_email(
        db,
        tenant,
        email,
        f"Su acceso al portal de clientes de {tenant.name}",
        f"<p>Hola{(' ' + e(name)) if name else ''}:</p>"
        f"<p>{e(tenant.name)} le dio acceso al portal de clientes de <b>{e(customer.name)}</b> "
        f"({e(ROLE_LABEL[role])}). Ahí puede reportar fallas, ver el avance de sus tickets y sus equipos instalados.</p>"
        f'<p><a href="{link}">Crear mi contraseña</a> (el enlace vence en {INVITE_DAYS} días y sirve una sola vez)</p>',
        "invitation",
        inv.id,
    )
    return {"id": inv.id, "link": link, "emailed": m.status == "enviado", "expires_at": inv.expires_at}


def suggestions(db: Session, r: AccessRequest, limit: int = 5) -> list[dict]:
    """Clientes que PODRIAN ser el de la solicitud, con el motivo. Es solo una ayuda: el humano confirma."""
    tid = r.tenant_id
    hits: dict[int, set[str]] = {}

    def add(cid: int, motivo: str) -> None:
        hits.setdefault(cid, set()).add(motivo)

    email = (r.email or "").lower()
    dominio = email.split("@")[-1] if "@" in email else ""
    for c in db.scalars(select(Customer).where(Customer.tenant_id == tid, func.lower(Customer.email) == email)):
        add(c.id, "correo")
    for k in db.scalars(select(CustomerContact).where(CustomerContact.tenant_id == tid, func.lower(CustomerContact.email) == email)):
        add(k.customer_id, "correo de contacto")
    genericos = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com", "live.com", "racsa.co.cr", "ice.co.cr"}
    if dominio and dominio not in genericos:
        for c in db.scalars(select(Customer).where(Customer.tenant_id == tid, func.lower(Customer.email).like(f"%@{dominio}")).limit(10)):
            add(c.id, "dominio del correo")
    tel = digitos(r.phone, 8)
    if len(tel) == 8:
        for c in db.scalars(select(Customer).where(Customer.tenant_id == tid, (Customer.phone.is_not(None)) | (Customer.whatsapp.is_not(None)))):
            if tel in (digitos(c.phone, 8), digitos(c.whatsapp, 8)):
                add(c.id, "teléfono")
        for k in db.scalars(select(CustomerContact).where(CustomerContact.tenant_id == tid, CustomerContact.phone.is_not(None))):
            if tel == digitos(k.phone, 8):
                add(k.customer_id, "teléfono de contacto")
    ced = digitos(r.id_number)
    if len(ced) >= 9:
        for c in db.scalars(select(Customer).where(Customer.tenant_id == tid, Customer.id_number.is_not(None))):
            if digitos(c.id_number) == ced:
                add(c.id, "cédula")
    if r.company and len(r.company) >= 4:
        for c in db.scalars(select(Customer).where(Customer.tenant_id == tid, Customer.name.ilike(f"%{r.company[:40]}%")).limit(5)):
            add(c.id, "nombre")
    peso = {"cédula": 5, "correo": 4, "correo de contacto": 4, "teléfono": 3, "teléfono de contacto": 3, "dominio del correo": 2, "nombre": 1}
    orden = sorted(hits.items(), key=lambda kv: -sum(peso.get(x, 1) for x in kv[1]))[:limit]
    out = []
    for cid, motivos in orden:
        c = db.get(Customer, cid)
        if c and c.tenant_id == tid:
            out.append({"id": c.id, "name": c.name, "id_number": c.id_number, "email": c.email, "motivos": sorted(motivos)})
    return out
