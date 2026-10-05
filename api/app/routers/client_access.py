"""Lado interno del portal del cliente: bandeja de solicitudes de acceso y usuarios-cliente por cliente.

Aprobar = dar acceso a datos de una empresa, asi que es del admin ("portal_clientes.aprobar"; el supervisor
solo ve la bandeja). El sistema SUGIERE clientes por correo, telefono o cedula, pero nunca liga solo: el
humano elige el cliente (o lo crea) y el rol.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import CLIENT_ROLES, Principal, require
from ..models import AccessRequest, Customer, Invitation, TenantUser, User
from ..services.client_access import invite_client, suggestions
from ..services.documents import audit
from ..services.mail import queue_email

router = APIRouter(tags=["portal del cliente (interno)"])


def _req(db: Session, rid: int, p: Principal) -> AccessRequest:
    r = db.get(AccessRequest, rid)
    if not r or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Solicitud no encontrada")
    return r


def _customer(db: Session, cid: int, p: Principal) -> Customer:
    c = db.get(Customer, cid)
    if not c or c.tenant_id != p.tenant.id:
        raise HTTPException(404, "Cliente no encontrado")
    return c


def _req_out(db: Session, r: AccessRequest, con_sugerencias: bool) -> dict:
    c = db.get(Customer, r.customer_id) if r.customer_id else None
    rev = db.get(User, r.reviewed_by) if r.reviewed_by else None
    out = {
        "id": r.id,
        "name": r.name,
        "email": r.email,
        "phone": r.phone,
        "company": r.company,
        "id_number": r.id_number,
        "message": r.message,
        "status": r.status,
        "customer_id": r.customer_id,
        "customer": c.name if c else None,
        "role": r.role_code,
        "reject_reason": r.reject_reason,
        "reviewed_by": rev.full_name if rev else None,
        "reviewed_at": r.reviewed_at,
        "created_at": r.created_at,
    }
    if con_sugerencias and r.status == "pendiente":
        out["suggestions"] = suggestions(db, r)
    return out


@router.get("/access-requests")
def list_requests(status: str = "pendiente", p: Principal = Depends(require("portal_clientes", "ver")), db: Session = Depends(get_db)):
    q = select(AccessRequest).where(AccessRequest.tenant_id == p.tenant.id)
    if status != "todas":
        q = q.where(AccessRequest.status == status)
    rows = db.scalars(q.order_by(AccessRequest.id.desc()).limit(200)).all()
    return [_req_out(db, r, con_sugerencias=True) for r in rows]


class NewCustomerIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    id_type: str = Field("juridica", pattern="^(fisica|juridica|dimex|nite|extranjero)$")
    id_number: str | None = Field(None, max_length=30)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=40)


class ApproveIn(BaseModel):
    customer_id: int | None = None
    new_customer: NewCustomerIn | None = None
    role: str = Field("cliente_usuario", pattern="^(cliente_admin|cliente_usuario)$")


@router.post("/access-requests/{rid}/approve")
def approve(rid: int, data: ApproveIn, p: Principal = Depends(require("portal_clientes", "aprobar")), db: Session = Depends(get_db)):
    r = _req(db, rid, p)
    if r.status != "pendiente":
        raise HTTPException(409, "Esta solicitud ya fue revisada")
    if bool(data.customer_id) == bool(data.new_customer):
        raise HTTPException(422, "Elija un cliente existente o cree uno nuevo")
    if data.customer_id:
        c = _customer(db, data.customer_id, p)
    else:
        n = data.new_customer
        c = Customer(
            tenant_id=p.tenant.id,
            name=n.name,
            id_type=n.id_type,
            id_number=n.id_number,
            email=str(n.email).lower() if n.email else None,
            phone=n.phone,
        )
        db.add(c)
        db.flush()
        audit(db, p.tenant.id, p.user.id, "create", "customer", c.id, {"desde": "solicitud de acceso"}, ip=p.ip)
    inv = invite_client(db, p.tenant, c, r.email, data.role, p.user.id, name=r.name)
    r.status, r.customer_id, r.role_code = "aprobada", c.id, data.role
    r.reviewed_by, r.reviewed_at, r.invitation_id = p.user.id, datetime.now(UTC), inv["id"]
    audit(db, p.tenant.id, p.user.id, "approve", "access_request", r.id, {"cliente": c.id, "rol": data.role}, ip=p.ip)
    db.commit()
    return {**inv, "customer_id": c.id, "customer": c.name}


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    notify: bool = True


@router.post("/access-requests/{rid}/reject")
def reject(rid: int, data: RejectIn, p: Principal = Depends(require("portal_clientes", "aprobar")), db: Session = Depends(get_db)):
    r = _req(db, rid, p)
    if r.status != "pendiente":
        raise HTTPException(409, "Esta solicitud ya fue revisada")
    r.status, r.reject_reason = "rechazada", data.reason
    r.reviewed_by, r.reviewed_at = p.user.id, datetime.now(UTC)
    audit(db, p.tenant.id, p.user.id, "reject", "access_request", r.id, ip=p.ip)
    emailed = False
    if data.notify:
        e = html.escape
        m = queue_email(
            db,
            p.tenant,
            r.email,
            f"Su solicitud de acceso a {p.tenant.name}",
            f"<p>Hola {e(r.name)}:</p><p>No pudimos aprobar su solicitud de acceso al portal de clientes.</p>"
            f"<p>{e(data.reason)}</p><p>Si cree que es un error, escríbanos y lo revisamos.</p>",
            "access_request",
            r.id,
        )
        emailed = m.status == "enviado"
    db.commit()
    return {"ok": True, "emailed": emailed}


# ---------- Usuarios del portal por cliente (la ficha del cliente y la bandeja) ----------
def portal_users(db: Session, tenant_id: int, customer_id: int) -> dict:
    ms = db.scalars(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.customer_id == customer_id).order_by(TenantUser.id)).all()
    inv = db.scalars(
        select(Invitation).where(
            Invitation.tenant_id == tenant_id,
            Invitation.customer_id == customer_id,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > datetime.now(UTC),
        )
    ).all()
    return {
        "users": [
            {"id": m.user.id, "name": m.user.full_name, "email": m.user.email, "role": m.role_code, "active": m.active, "last_login": m.user.last_login_at}
            for m in ms
        ],
        "invitations": [{"id": i.id, "email": i.email, "role": i.role_code, "expires_at": i.expires_at} for i in inv],
    }


@router.get("/customers/{cid}/portal")
def customer_portal(cid: int, p: Principal = Depends(require("portal_clientes", "ver")), db: Session = Depends(get_db)):
    c = _customer(db, cid, p)
    return {"customer": {"id": c.id, "name": c.name}, **portal_users(db, p.tenant.id, c.id)}


class InviteIn(BaseModel):
    email: EmailStr
    role: str = Field("cliente_usuario", pattern="^(cliente_admin|cliente_usuario)$")
    name: str | None = Field(None, max_length=120)


@router.post("/customers/{cid}/portal/invitations", status_code=201)
def customer_invite(cid: int, data: InviteIn, p: Principal = Depends(require("portal_clientes", "aprobar")), db: Session = Depends(get_db)):
    """Invitar directo a un contacto desde la ficha del cliente, sin que haya pedido acceso."""
    c = _customer(db, cid, p)
    inv = invite_client(db, p.tenant, c, str(data.email), data.role, p.user.id, name=data.name)
    audit(db, p.tenant.id, p.user.id, "invite", "customer_portal", c.id, {"rol": data.role}, ip=p.ip)
    db.commit()
    return inv


@router.post("/customers/{cid}/portal/invitations/{iid}/revoke")
def customer_invite_revoke(cid: int, iid: int, p: Principal = Depends(require("portal_clientes", "aprobar")), db: Session = Depends(get_db)):
    i = db.get(Invitation, iid)
    if not i or i.tenant_id != p.tenant.id or i.customer_id != cid or i.accepted_at:
        raise HTTPException(404, "Invitación no encontrada")
    i.expires_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}


class PortalUserIn(BaseModel):
    role: str | None = Field(None, pattern="^(cliente_admin|cliente_usuario)$")
    active: bool | None = None


@router.patch("/customers/{cid}/portal/users/{uid}")
def customer_user_update(cid: int, uid: int, data: PortalUserIn, p: Principal = Depends(require("portal_clientes", "aprobar")), db: Session = Depends(get_db)):
    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == uid, TenantUser.customer_id == cid))
    if not m or m.role_code not in CLIENT_ROLES:
        raise HTTPException(404, "Usuario no encontrado")
    if data.role:
        m.role_code = data.role
    if data.active is not None:
        m.active = data.active
    audit(db, p.tenant.id, p.user.id, "update", "customer_portal_user", uid, data.model_dump(exclude_none=True), ip=p.ip)
    db.commit()
    return {"ok": True}
