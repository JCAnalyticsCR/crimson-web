"""Ajustes (plan 3.8): facturacion, notificaciones, metodos manuales, cuentas bancarias, grupos, usuarios e invitaciones, pasarelas, correo."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings as cfg
from ..core.crypto import encrypt, mask
from ..core.db import get_db
from ..core.deps import ASSIGNABLE_ROLES, Principal, require
from ..core.security import hash_password, hash_token, password_is_strong
from ..models import BankAccount, BillingGroup, EmailOutbox, Invitation, PaymentGatewayConfig, Tenant, TenantUser, User
from ..services.mail import queue_email

router = APIRouter(prefix="/settings", tags=["ajustes"])

DEFAULTS = {
    "invoice_valid_days": 8,
    "quote_valid_days": 15,
    "invoice_footer": "",
    "quote_footer": "",
    "notify_due": True,
    "remind_days_before": 3,
    "daily_close_email": False,
    "bcc": [],
    "manual_payment_methods": [
        {"name": "SINPE Móvil", "instructions": "Envíe el pago al número indicado y comparta el comprobante.", "active": True},
        {"name": "Transferencia", "instructions": "Cuenta IBAN indicada en la factura.", "active": True},
        {"name": "Efectivo", "instructions": "Pago en efectivo contra entrega.", "active": True},
    ],
    "activity_codes": ["6202.0"],
    "einvoice_provider": cfg.einvoice_provider,
    "phones": [],
    "addresses": [],
    "social": {},
    "legal": {"privacy": "", "terms": ""},
}


class SettingsIn(BaseModel):
    invoice_valid_days: int | None = Field(None, ge=0, le=365)
    quote_valid_days: int | None = Field(None, ge=0, le=365)
    invoice_footer: str | None = None
    quote_footer: str | None = None
    notify_due: bool | None = None
    remind_days_before: int | None = Field(None, ge=0, le=60)
    daily_close_email: bool | None = None
    bcc: list[EmailStr] | None = None
    manual_payment_methods: list[dict] | None = None
    activity_codes: list[str] | None = None
    einvoice_provider: str | None = Field(None, pattern="^(none|sandbox|alanube|gti)$")
    phones: list[dict] | None = None
    addresses: list[dict] | None = None
    social: dict | None = None
    legal: dict | None = None


@router.get("")
def get_settings(p: Principal = Depends(require("settings", "ver"))):
    return {**DEFAULTS, **(p.tenant.settings or {})}


@router.put("")
def put_settings(data: SettingsIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    t = db.get(Tenant, p.tenant.id)
    t.settings = {**DEFAULTS, **(t.settings or {}), **data.model_dump(exclude_unset=True, mode="json")}
    db.commit()
    return t.settings


# ---------- Cuentas bancarias ----------
class BankIn(BaseModel):
    name: str
    bank: str | None = None
    currency: str = "CRC"
    number: str | None = None
    active: bool = True


@router.get("/bank-accounts")
def banks(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    return [
        {"id": b.id, "name": b.name, "bank": b.bank, "currency": b.currency, "number": b.number, "active": b.active}
        for b in db.scalars(select(BankAccount).where(BankAccount.tenant_id == p.tenant.id).order_by(BankAccount.id))
    ]


@router.post("/bank-accounts", status_code=201)
def bank_create(data: BankIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    b = BankAccount(tenant_id=p.tenant.id, **data.model_dump())
    db.add(b)
    db.commit()
    return {"id": b.id}


@router.put("/bank-accounts/{bid}")
def bank_update(bid: int, data: BankIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    b = db.get(BankAccount, bid)
    if not b or b.tenant_id != p.tenant.id:
        raise HTTPException(404, "Cuenta no encontrada")
    for k, v in data.model_dump().items():
        setattr(b, k, v)
    db.commit()
    return {"id": b.id}


# ---------- Grupos de facturacion ----------
class GroupIn(BaseModel):
    prefix: str = Field(min_length=1, max_length=10)
    branch: str = Field("001", pattern=r"^\d{3}$")
    terminal: str = Field("00001", pattern=r"^\d{5}$")
    current: int | None = Field(None, ge=0)
    is_default: bool | None = None


@router.put("/billing-groups/{gid}")
def group_update(gid: int, data: GroupIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    g = db.get(BillingGroup, gid)
    if not g or g.tenant_id != p.tenant.id:
        raise HTTPException(404, "Grupo no encontrado")
    if data.current is not None and data.current < g.current:
        raise HTTPException(422, "El consecutivo nunca retrocede (Hacienda no permite reutilizar)")
    g.prefix, g.branch, g.terminal = data.prefix, data.branch, data.terminal
    if data.current is not None:
        g.current = data.current
    if data.is_default:
        for other in db.scalars(select(BillingGroup).where(BillingGroup.tenant_id == p.tenant.id, BillingGroup.doc_type == g.doc_type)):
            other.is_default = other.id == g.id
    db.commit()
    return {"ok": True}


# ---------- Usuarios e invitaciones ----------
@router.get("/users")
def users(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    rows = db.scalars(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id)).all()
    inv = db.scalars(
        select(Invitation).where(Invitation.tenant_id == p.tenant.id, Invitation.accepted_at.is_(None), Invitation.expires_at > datetime.now(UTC))
    ).all()
    return {
        "users": [
            {
                "id": m.user.id,
                "email": m.user.email,
                "name": m.user.full_name,
                "role": m.role_code,
                "active": m.active,
                "totp": m.user.totp_enabled,
                "last_login": m.user.last_login_at,
            }
            for m in rows
        ],
        "invitations": [{"id": i.id, "email": i.email, "role": i.role_code, "expires_at": i.expires_at} for i in inv],
        "roles": ASSIGNABLE_ROLES,
    }


class InviteIn(BaseModel):
    email: EmailStr
    role: str = "lectura"


@router.post("/invitations", status_code=201)
def invite(data: InviteIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    if data.role not in ASSIGNABLE_ROLES:
        raise HTTPException(422, "Rol invalido")
    raw = secrets.token_urlsafe(32)
    inv = Invitation(
        tenant_id=p.tenant.id,
        email=data.email.lower(),
        role_code=data.role,
        token_hash=hash_token(raw),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        invited_by=p.user.id,
    )
    db.add(inv)
    db.flush()
    link = f"{cfg.public_base_url}/invitacion/{raw}"
    queue_email(
        db,
        p.tenant,
        data.email,
        f"Invitación a {p.tenant.name} en Crimson",
        f'<p>Te invitaron a <b>{p.tenant.name}</b> con rol <b>{data.role}</b>.</p><p><a href="{link}">Aceptar invitación</a> (vence en 7 días)</p>',
        "invitation",
        inv.id,
    )
    db.commit()
    return {"id": inv.id, "link": link}  # el enlace tambien se devuelve para compartirlo por WhatsApp si el correo no esta configurado


class AcceptIn(BaseModel):
    token: str
    full_name: str = Field(min_length=2)
    password: str


@router.post("/invitations/accept", tags=["publico"])
def accept(data: AcceptIn, db: Session = Depends(get_db)):
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == hash_token(data.token)))
    if not inv or inv.accepted_at or inv.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
        raise HTTPException(404, "Invitación inválida o vencida")
    if not password_is_strong(data.password):
        raise HTTPException(422, "Mínimo 10 caracteres con letras y números")
    u = db.scalar(select(User).where(User.email == inv.email))
    if not u:
        u = User(email=inv.email, full_name=data.full_name, password_hash=hash_password(data.password))
        db.add(u)
        db.flush()
    if not db.scalar(select(TenantUser).where(TenantUser.user_id == u.id, TenantUser.tenant_id == inv.tenant_id)):
        db.add(TenantUser(tenant_id=inv.tenant_id, user_id=u.id, role_code=inv.role_code))
    inv.accepted_at = datetime.now(UTC)
    db.commit()
    return {"ok": True, "email": u.email}


class MemberIn(BaseModel):
    role: str | None = None
    active: bool | None = None


@router.patch("/users/{uid}")
def member_update(uid: int, data: MemberIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == p.tenant.id, TenantUser.user_id == uid))
    if not m:
        raise HTTPException(404, "Usuario no encontrado")
    if uid == p.user.id and (data.active is False or (data.role and data.role != "admin")):
        raise HTTPException(409, "No podés quitarte el acceso de administrador a vos mismo")
    if data.role:
        if data.role not in ASSIGNABLE_ROLES:
            raise HTTPException(422, "Rol invalido")
        m.role_code = data.role
    if data.active is not None:
        m.active = data.active
    db.commit()
    return {"ok": True}


# ---------- Pasarelas ----------
class GatewayIn(BaseModel):
    provider: str = Field(pattern="^(onvo|paypal)$")
    client_id: str | None = None
    secret: str | None = None  # se cifra; nunca se devuelve
    is_primary: bool = False
    active: bool = False
    mode: str = Field("test", pattern="^(test|live)$")


@router.get("/gateways")
def gateways(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    return [
        {
            "id": g.id,
            "provider": g.provider,
            "client_id": g.client_id,
            "secret_mask": mask(g.secret_encrypted and "configurado00"),
            "is_primary": g.is_primary,
            "active": g.active,
            "mode": g.mode,
        }
        for g in db.scalars(select(PaymentGatewayConfig).where(PaymentGatewayConfig.tenant_id == p.tenant.id))
    ]


@router.put("/gateways")
def gateway_upsert(data: GatewayIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    g = db.scalar(select(PaymentGatewayConfig).where(PaymentGatewayConfig.tenant_id == p.tenant.id, PaymentGatewayConfig.provider == data.provider))
    if not g:
        g = PaymentGatewayConfig(tenant_id=p.tenant.id, provider=data.provider)
        db.add(g)
    g.client_id, g.is_primary, g.active, g.mode = data.client_id, data.is_primary, data.active, data.mode
    if data.secret:
        g.secret_encrypted = encrypt(data.secret)
    db.commit()
    return {"ok": True}


# ---------- Correo saliente ----------
@router.get("/outbox")
def outbox(limit: int = 30, p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    rows = db.scalars(select(EmailOutbox).where(EmailOutbox.tenant_id == p.tenant.id).order_by(EmailOutbox.id.desc()).limit(limit)).all()
    return [
        {
            "id": m.id,
            "to": m.to,
            "subject": m.subject,
            "status": m.status,
            "entity": m.entity,
            "entity_id": m.entity_id,
            "sent_at": m.sent_at,
            "error": m.error,
            "created_at": m.created_at,
        }
        for m in rows
    ]


# ---------- Bandeja IMAP de XML de proveedores ----------
class InboxIn(BaseModel):
    enabled: bool = False
    host: str = Field("", max_length=200)
    port: int = Field(993, ge=1, le=65535)
    user: str = Field("", max_length=200)
    password: str | None = Field(None, max_length=300)  # vacio = conservar la guardada
    folder: str = Field("INBOX", max_length=120)


def _inbox_out(t: Tenant) -> dict:
    from ..services.inbox import config

    c = config(t)
    return {k: v for k, v in c.items() if k != "password_enc"} | {"has_password": bool(c.get("password_enc"))}


@router.get("/inbox")
def inbox_get(p: Principal = Depends(require("settings", "ver"))):
    return _inbox_out(p.tenant)


@router.put("/inbox")
def inbox_put(data: InboxIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    from sqlalchemy.orm.attributes import flag_modified

    from ..services.inbox import config

    cur = config(p.tenant)
    new = {**cur, **data.model_dump(exclude={"password"})}
    if data.password:
        new["password_enc"] = encrypt(data.password)
    st = dict(p.tenant.settings or {})
    st["inbox"] = new
    p.tenant.settings = st
    flag_modified(p.tenant, "settings")
    db.commit()
    return _inbox_out(p.tenant)


@router.post("/inbox/run")
def inbox_run(p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    """Revisa la bandeja ahora (el worker lo hace cada 15 minutos si esta activa)."""
    from ..services.inbox import poll

    try:
        return poll(db, p.tenant)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except OSError as e:
        raise HTTPException(502, f"No se pudo conectar al servidor de correo: {e}") from e
    except Exception as e:  # noqa: BLE001 - imaplib.IMAP4.error (credenciales) y similares
        raise HTTPException(502, f"El servidor de correo rechazó la conexión: {e}") from e
