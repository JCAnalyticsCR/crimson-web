"""Ajustes (plan 3.8): facturacion, notificaciones, metodos manuales, cuentas bancarias, grupos, pasarelas, correo.
Usuarios e invitaciones viven en routers/users_admin.py."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings as cfg
from ..core.crypto import encrypt, mask
from ..core.db import get_db
from ..core.deps import COST_ROLE_CHOICES, Principal, require
from ..models import BankAccount, BillingGroup, EmailOutbox, PaymentGatewayConfig, Tenant
from ..providers.payments import OnvoAdapter, OnvoError, key_mode, key_problem, webhook_secret_problem
from ..services import onvo
from ..services.bitacora import audit_change
from ..services.documents import audit
from ..services.mail import mail_status, queue_email
from ..services.sla import SLA_DEFECTO, normalizar

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
    "max_discount_pct": 10,
    "default_margin_pct": 35,  # margen con el que se sugiere el precio de venta desde el costo
    "labor_day_cost": 25000,  # costo interno por tecnico por dia
    "travel_cost": 35000,  # transporte estimado por trabajo
    "fuel_cost_per_km": 0,  # si se cobra por kilometro, el transporte se calcula con esto
    "quote_approval_amount": 2000000,  # cotizacion por encima -> aprobacion
    "min_margin_pct": 25,  # margen por debajo -> aprobacion
    # SLA de soporte por prioridad (horas). Un contrato con SLA propio lo reemplaza para ese cliente.
    "sla": SLA_DEFECTO,
    "archive_purge_days": 30,  # dias en la papelera antes de que el worker lo borre definitivamente
    # inventario: apagado = todo como siempre (ventas, POS, OT y ajustes pueden dejar stock negativo)
    "prevent_negative_stock": False,
    # reservas: 0 = toda reserva requiere aprobacion; X = las de proyectos hasta X unidades se confirman solas
    "reservation_auto_approve_units": 0,
    "reservation_days": 15,  # vigencia por defecto de una reserva
    "cost_roles": [],  # roles que, ademas del admin, ven costos (Andres 08:07: por defecto nadie)
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
    max_discount_pct: float | None = Field(None, ge=0, le=100)
    default_margin_pct: float | None = Field(None, ge=0, le=95)
    labor_day_cost: float | None = Field(None, ge=0)
    labor_day_cost_civil: float | None = Field(None, ge=0)
    labor_day_cost_contratado: float | None = Field(None, ge=0)
    per_diem_cost: float | None = Field(None, ge=0)
    travel_cost: float | None = Field(None, ge=0)
    fuel_cost_per_km: float | None = Field(None, ge=0)
    quote_approval_amount: float | None = Field(None, ge=0)
    min_margin_pct: float | None = Field(None, ge=0, le=95)
    sla: dict | None = None

    @field_validator("sla")
    @classmethod
    def _sla(cls, v):
        if v is None:
            return None
        n = normalizar(v)
        # se exige la tabla completa: un SLA a medias deja prioridades midiendose contra algo que nadie eligio
        if not n or len(n) != len(SLA_DEFECTO):
            raise ValueError("SLA invalido: las 4 prioridades con horas de respuesta y resolucion (1 a 2160)")
        return n
    archive_purge_days: int | None = Field(None, ge=1, le=365)
    prevent_negative_stock: bool | None = None
    reservation_auto_approve_units: float | None = Field(None, ge=0, le=100000)
    reservation_days: int | None = Field(None, ge=1, le=365)
    # Ajustes -> Usuarios -> "Quien ve costos": roles que, ademas del admin, ven costo, margen y utilidad
    cost_roles: list[str] | None = None

    @field_validator("cost_roles")
    @classmethod
    def _cost_roles(cls, v):
        if v is None:
            return None
        malos = [r for r in v if r not in COST_ROLE_CHOICES]
        if malos:
            # ventas, caja, tecnicos, aliados y clientes nunca ven costos, ni por ajuste (Andres 08:07)
            raise ValueError(f"Estos roles no pueden ver costos: {', '.join(malos)}")
        return list(dict.fromkeys(v))


@router.get("")
def get_settings(p: Principal = Depends(require("settings", "ver"))):
    return {**DEFAULTS, **(p.tenant.settings or {})}


@router.put("")
def put_settings(data: SettingsIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    t = db.get(Tenant, p.tenant.id)
    antes = list((t.settings or {}).get("cost_roles") or [])
    t.settings = {**DEFAULTS, **(t.settings or {}), **data.model_dump(exclude_unset=True, mode="json")}
    if "cost_roles" in data.model_fields_set:
        # quien ve costos es un permiso: queda en la bitacora con antes/despues (solo si cambio). Aplica en la
        # siguiente peticion: los permisos se calculan en cada request, no hace falta cerrar sesiones.
        despues = list(t.settings.get("cost_roles") or [])
        audit_change(db, p, "permissions", "tenant", t.id, {"cost_roles": antes}, {"cost_roles": despues}, reason="Quien ve costos")
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


# ---------- Usuarios e invitaciones: routers/users_admin.py (mismas rutas /settings/users e /settings/invitations) ----------


# ---------- Pasarelas ----------
class GatewayIn(BaseModel):
    provider: str = Field(pattern="^(onvo|paypal)$")
    client_id: str | None = Field(None, max_length=200)  # ONVO: llave publicable (opcional; el Checkout alojado no la usa)
    secret: str | None = Field(None, max_length=300)  # se cifra; nunca se devuelve. Vacio = no cambiar
    webhook_secret: str | None = Field(None, max_length=300)  # ONVO: X-Webhook-Secret. Vacio = no cambiar
    is_primary: bool = False
    active: bool = False
    mode: str = Field("test", pattern="^(test|live)$")  # ONVO lo ignora: el modo sale de la llave

    @field_validator("client_id", "secret", "webhook_secret")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return (v or "").strip() or None


def _key_mask(g: PaymentGatewayConfig) -> str | None:
    """ONVO: prefijo + ultimos 4 (identifica la llave sin exponerla). Otras pasarelas: solo "configurado"."""
    if not g.secret_encrypted:
        return None
    raw = onvo._dec(g.secret_encrypted) if g.provider == "onvo" else None
    if raw and key_mode(raw):
        return f"{raw[: raw.index('_key_') + 5]}••••{raw[-4:]}"
    return mask("configurado00")


@router.get("/gateways")
def gateways(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    return [
        {
            "id": g.id,
            "provider": g.provider,
            "client_id": g.client_id,
            "secret_mask": _key_mask(g),
            "webhook_secret_set": bool(g.webhook_secret_encrypted),
            "is_primary": g.is_primary,
            "active": g.active,
            "mode": g.mode,
        }
        for g in db.scalars(select(PaymentGatewayConfig).where(PaymentGatewayConfig.tenant_id == p.tenant.id))
    ]


@router.put("/gateways")
def gateway_upsert(data: GatewayIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    if data.provider == "onvo":
        for val, problem in (
            (data.secret, data.secret and key_problem(data.secret, "secret")),
            (data.client_id, data.client_id and key_problem(data.client_id, "publishable")),
            (data.webhook_secret, data.webhook_secret and webhook_secret_problem(data.webhook_secret)),
        ):
            if val and problem:
                raise HTTPException(422, problem)
        if data.secret and data.client_id and key_mode(data.secret) != key_mode(data.client_id):
            raise HTTPException(422, "La llave secreta y la publicable son de modos distintos (una de pruebas y otra de producción)")
        if data.secret and cfg.is_prod and key_mode(data.secret) == "test":
            raise HTTPException(422, "En producción va la llave de producción (onvo_live_secret_key_…): con la de pruebas nadie pagaría de verdad")
    g = db.scalar(select(PaymentGatewayConfig).where(PaymentGatewayConfig.tenant_id == p.tenant.id, PaymentGatewayConfig.provider == data.provider))
    if not g:
        g = PaymentGatewayConfig(tenant_id=p.tenant.id, provider=data.provider)
        db.add(g)
    antes = {"active": g.active, "mode": g.mode, "secret": bool(g.secret_encrypted), "webhook": bool(g.webhook_secret_encrypted)}
    g.client_id, g.is_primary, g.active, g.mode = data.client_id, data.is_primary, data.active, data.mode
    if data.secret:
        g.secret_encrypted = encrypt(data.secret)
    if data.webhook_secret:
        g.webhook_secret_encrypted = encrypt(data.webhook_secret)
    if data.provider == "onvo":
        g.mode = key_mode(onvo._dec(g.secret_encrypted)) or g.mode
        if g.active and not g.secret_encrypted:
            raise HTTPException(422, "Para activar ONVO falta la llave secreta")
    db.flush()
    despues = {"active": g.active, "mode": g.mode, "secret_nuevo": bool(data.secret), "webhook_nuevo": bool(data.webhook_secret)}
    audit(db, p.tenant.id, p.user.id, "gateway_update", "gateway", g.id, {"provider": g.provider, "antes": antes, "despues": despues})
    db.commit()
    return {"ok": True}


@router.get("/gateways/onvo/status")
def gateway_onvo_status(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    return onvo.status(db, p.tenant)


@router.post("/gateways/onvo/test")
def gateway_onvo_test(p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    """Prueba la llave guardada (aunque la pasarela este inactiva) con una lectura que no crea nada en ONVO."""
    g = onvo.gateway(db, p.tenant.id)
    key = (onvo._dec(g.secret_encrypted) if g and g.secret_encrypted else None) or cfg.onvo_secret_key
    if not key:
        return {"ok": False, "mode": None, "detail": "No hay llave secreta de ONVO guardada"}
    if not key_mode(key):
        return {"ok": False, "mode": None, "detail": "La llave guardada no tiene el formato de ONVO"}
    try:
        OnvoAdapter(key).ping()
    except OnvoError as e:
        hint = " (revisá que sea la llave secreta y que esté vigente)" if e.status in (401, 403) else ""
        return {"ok": False, "mode": key_mode(key), "detail": f"ONVO respondió {e.status or 'sin conexión'}: {e.message}{hint}"}
    return {"ok": True, "mode": key_mode(key), "detail": f"Conectado a ONVO en modo {'pruebas' if key_mode(key) == 'test' else 'producción'}"}


# ---------- Correo saliente ----------
@router.get("/mail-status")
def mail_status_get(p: Principal = Depends(require("settings", "ver"))):
    return mail_status()


@router.post("/mail-test")
def mail_test(p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    """Manda un correo de prueba a quien lo pide y dice si salio de verdad (o el error exacto del proveedor)."""
    m = queue_email(db, p.tenant, p.user.email, "Prueba de correo · Crimson",
                    '<p>Si lee esto, el correo saliente de Crimson funciona.</p>', "mail_test", None)
    db.commit()
    return {"to": m.to, "status": m.status, "error": m.error}


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
