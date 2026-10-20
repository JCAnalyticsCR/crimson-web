"""Ajustes -> Hacienda: identificacion y ubicacion del emisor, actividades, credenciales por ambiente (cifradas, solo se
escriben), consecutivos fiscales, modo y el interruptor de produccion.

Lo que nunca sale: usuario ATV, contrasena, PIN, .p12 ni texto cifrado (solo banderas y metadatos del certificado).
Contabilidad VE el estado (settings.ver o accounting.ver) pero no carga credenciales, ni cambia el modo, ni los
consecutivos, ni activa produccion.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..core import secretbox
from ..core.config import settings as cfg
from ..core.db import get_db
from ..core.deps import Principal, get_principal, require, require_one_of
from ..core.ratelimit import hacienda_test_limiter
from ..core.reauth import require_reauth
from ..models import FiscalActivity, FiscalCredential
from ..providers.hacienda import xml44
from ..providers.hacienda.client import (
    HaciendaAuthError,
    HaciendaClient,
    HaciendaUnavailable,
    ProductionBlocked,
    instance_fingerprint,
    lookup_activities,
    production_keys,
)
from ..providers.hacienda.xades import CertError, load_p12
from ..services import hacienda_config as hc
from ..services.bitacora import audit_change, snapshot
from ..services.cr_geo import geo_catalog
from ..services.documents import audit

router = APIRouter(tags=["hacienda"])
ENVS = ("pruebas", "produccion")
MAX_P12 = 64 * 1024


def _env(environment: str) -> str:
    if environment not in ENVS:
        raise HTTPException(404, "Ambiente desconocido (pruebas o produccion)")
    return environment


def _activity_out(a: FiscalActivity) -> dict:
    return {"id": a.id, "code": a.code, "description": a.description, "is_default": a.is_default, "active": a.active}


def _activities(db: Session, tenant_id: int, only_active: bool = False) -> list[FiscalActivity]:
    q = select(FiscalActivity).where(FiscalActivity.tenant_id == tenant_id)
    if only_active:
        q = q.where(FiscalActivity.active)
    return db.scalars(q.order_by(FiscalActivity.is_default.desc(), FiscalActivity.code)).all()


def _profile_out(db: Session, p: Principal) -> dict:
    prof = hc.profile(db, p.tenant)
    return {
        "id_type": prof.id_type,
        "tax_id": p.tenant.tax_id,
        "legal_name": p.tenant.legal_name,
        "commercial_name": prof.commercial_name,
        "email": prof.email,
        "phone_country": prof.phone_country,
        "phone": prof.phone,
        "province": prof.province,
        "canton": prof.canton,
        "district": prof.district,
        "district_name": prof.district_name,
        "barrio": prof.barrio,
        "other_signs": prof.other_signs,
    }


def hacienda_state(db: Session, p: Principal) -> dict:
    from ..services.inbox import status as inbox_status

    prof = hc.profile(db, p.tenant)
    effective, blocked = hc.production_state(db, p.tenant)
    st = inbox_status(db, p.tenant)
    out = {
        "mode": hc.hacienda_mode(db, p.tenant),
        "einvoice_provider": {"alanube": "none", "gti": "none"}.get(hc.provider_setting(p.tenant), hc.provider_setting(p.tenant)),
        "environment": prof.environment,
        "profile": _profile_out(db, p),
        "activities": [_activity_out(a) for a in _activities(db, p.tenant.id)],
        "credentials": {env: hc.credentials_block(db, p.tenant, env) for env in ENVS},
        "consecutives": hc.consecutives_block(db, p.tenant),
        "consecutives_confirmed_at": prof.consecutives_confirmed_at,
        "production": {
            "allowed_by_server": bool(cfg.hacienda_production_allowed and cfg.env == "production"),
            "enabled_at": prof.production_enabled_at,
            "effective": effective,
            "blocked_reason": blocked,
            "checks": hc.readiness(db, p.tenant, "produccion"),
        },
        "imap": {"source": st["source"], "mailbox": st["mailbox"], "last_run_at": st["last_run_at"], "last_result": st["last_result"]},
        "can_configure": p.can("settings", "configurar"),
    }
    db.commit()  # perfil y grupos fiscales que falten se crean al leer
    return out


@router.get("/settings/hacienda")
def get_hacienda(p: Principal = Depends(require_one_of(("settings", "ver"), ("accounting", "ver"))), db: Session = Depends(get_db)):
    return hacienda_state(db, p)


# ---------------------------------------------------------------- perfil
ID_DIGITS = {"01": (9, 9), "02": (10, 10), "03": (11, 12), "04": (10, 10)}


class ProfileIn(BaseModel):
    id_type: str = Field(pattern="^0[1-4]$")
    tax_id: str = Field(min_length=9, max_length=30)
    legal_name: str = Field(min_length=5, max_length=100)
    commercial_name: str | None = Field(None, max_length=80)
    email: EmailStr
    phone_country: str = Field("506", pattern=r"^\d{1,3}$")
    phone: str | None = Field(None, max_length=20)
    province: str = Field(pattern="^[1-7]$")
    canton: str = Field(pattern=r"^\d{2}$")
    district: str = Field(pattern=r"^\d{2}$")
    district_name: str | None = Field(None, max_length=80)
    barrio: str | None = Field(None, max_length=50)
    other_signs: str = Field(min_length=5, max_length=250)

    @field_validator("commercial_name", "phone", "district_name", "barrio")
    @classmethod
    def _blank(cls, v):
        return (v or "").strip() or None

    @field_validator("barrio")
    @classmethod
    def _barrio(cls, v):
        if v is not None and not (5 <= len(v) <= 50):
            raise ValueError("El barrio va de 5 a 50 caracteres (o vacío)")
        return v


def _check_tax_id(id_type: str, tax_id: str) -> None:
    d = re.sub(r"\D", "", tax_id)
    lo, hi = ID_DIGITS[id_type]
    if not (lo <= len(d) <= hi) or (id_type == "01" and d.startswith("0")):
        name = {"01": "física: 9 dígitos sin cero inicial", "02": "jurídica: 10 dígitos", "03": "DIMEX: 11 o 12 dígitos", "04": "NITE: 10 dígitos"}[id_type]
        raise HTTPException(422, f"Cédula inválida para el tipo ({name})")


@router.put("/settings/hacienda/profile")
def put_profile(data: ProfileIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    _check_tax_id(data.id_type, data.tax_id)
    prof = hc.profile(db, p.tenant)
    antes = {**snapshot(prof), "tax_id": p.tenant.tax_id, "legal_name": p.tenant.legal_name}
    id_changed = xml44.digits(data.tax_id) != xml44.digits(p.tenant.tax_id) or data.id_type != prof.id_type
    if id_changed and hc.production_on(db, p.tenant):
        raise HTTPException(409, "Desactive producción primero")
    for k in ("id_type", "commercial_name", "phone_country", "phone", "province", "canton", "district", "district_name", "barrio", "other_signs"):
        setattr(prof, k, getattr(data, k))
    prof.email = str(data.email)
    p.tenant.tax_id, p.tenant.legal_name = data.tax_id.strip(), data.legal_name.strip()
    if id_changed:
        # la clave, los mensajes y el certificado dependen de la cedula: hay que volver a confirmar y a probar
        prof.consecutives_confirmed_at, prof.consecutives_confirmed_by = None, None
        for c in db.scalars(select(FiscalCredential).where(FiscalCredential.tenant_id == p.tenant.id)):
            c.last_test_at, c.last_test_ok, c.last_test_detail = None, None, None
    audit_change(db, p, "update", "fiscal_profile", prof.id, antes, {**snapshot(prof), "tax_id": p.tenant.tax_id, "legal_name": p.tenant.legal_name}, reason="Ajustes → Hacienda")
    db.commit()
    return hacienda_state(db, p)


class ModeIn(BaseModel):
    mode: str = Field(pattern="^(none|sandbox|pruebas|produccion)$")
    confirm_text: str | None = None  # para entrar a Pruebas: escribir PRUEBAS (plan 1.11)


PRUEBAS_RIESGO = "cada venta real se emitiría al ambiente de pruebas y después no se podría emitir en producción"


@router.put("/settings/hacienda/mode")
def put_mode(data: ModeIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    if data.mode == "produccion":
        raise HTTPException(422, "Producción se activa con el interruptor")
    antes = hc.hacienda_mode(db, p.tenant)
    # Pruebas en la empresa real quema cada venta (plan 4.6): se confirma por escrito. Para probar la conexion sin
    # tocar las ventas: python -m app.tools.hacienda_smoke --sin-modo (no cambia el modo)
    if data.mode == "pruebas" and antes != "pruebas" and (data.confirm_text or "").strip().upper() != "PRUEBAS":
        raise HTTPException(422, f"Escriba PRUEBAS para confirmar: {PRUEBAS_RIESGO}")
    st = dict(p.tenant.settings or {})
    st["einvoice_provider"] = {"none": "none", "sandbox": "sandbox", "pruebas": "hacienda"}[data.mode]
    p.tenant.settings = st
    flag_modified(p.tenant, "settings")
    hc.turn_off_production(db, p.tenant, p.user.id, ip=p.ip, reason=f"modo {data.mode}")  # cualquier modo apaga produccion
    audit(db, p.tenant.id, p.user.id, "hacienda_mode", "tenant", p.tenant.id, {"antes": antes, "despues": data.mode}, ip=p.ip)
    db.commit()
    return hacienda_state(db, p)


# ---------------------------------------------------------------- actividades
@router.get("/fiscal/activities")
def fiscal_activities(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return [_activity_out(a) for a in _activities(db, p.tenant.id, only_active=True)]


@router.get("/fiscal/geo")
def fiscal_geo(p: Principal = Depends(get_principal)):
    return geo_catalog()


@router.get("/fiscal/mode")
def fiscal_mode(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Modo fiscal vigente (lo usa el editor de facturas: en emision directa la fecha la pone la emision)."""
    return {"mode": hc.hacienda_mode(db, p.tenant)}


class ActivityIn(BaseModel):
    code: str = Field(pattern=r"^[0-9A-Za-z.]{6}$")
    description: str | None = Field(None, max_length=200)
    is_default: bool = False
    active: bool = True


def _sync_legacy_codes(db: Session, p: Principal) -> None:
    """Compatibilidad: tenant.settings["activity_codes"] sigue la lista (lo leia services/einvoice.py)."""
    st = dict(p.tenant.settings or {})
    st["activity_codes"] = [a.code for a in _activities(db, p.tenant.id, only_active=True)]
    p.tenant.settings = st
    flag_modified(p.tenant, "settings")


def _apply_default(db: Session, p: Principal, a: FiscalActivity) -> None:
    if a.is_default:
        for o in db.scalars(select(FiscalActivity).where(FiscalActivity.tenant_id == p.tenant.id, FiscalActivity.id != a.id)):
            o.is_default = False


@router.post("/settings/hacienda/activities", status_code=201)
def activity_create(data: ActivityIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    old = db.scalar(select(FiscalActivity).where(FiscalActivity.tenant_id == p.tenant.id, FiscalActivity.code == data.code))
    if old is not None and old.active:
        raise HTTPException(409, "Esa actividad ya está en la lista")
    first = not db.scalar(select(FiscalActivity.id).where(FiscalActivity.tenant_id == p.tenant.id, FiscalActivity.active, FiscalActivity.is_default))
    if old is not None:
        # "Quitar" solo la desactiva (las respuestas viejas la referencian): agregarla de nuevo la reactiva, misma fila
        antes = snapshot(old)
        old.active, old.is_default = True, data.is_default or first
        if data.description is not None:
            old.description = data.description
        _apply_default(db, p, old)
        _sync_legacy_codes(db, p)
        audit_change(db, p, "reactivate", "fiscal_activity", old.id, antes, snapshot(old))
        db.commit()
        return _activity_out(old)
    a = FiscalActivity(tenant_id=p.tenant.id, code=data.code, description=data.description, is_default=data.is_default or first, active=True)
    db.add(a)
    db.flush()
    _apply_default(db, p, a)
    _sync_legacy_codes(db, p)
    audit(db, p.tenant.id, p.user.id, "create", "fiscal_activity", a.id, {"code": a.code}, ip=p.ip)
    db.commit()
    return _activity_out(a)


def _activity(db: Session, p: Principal, aid: int) -> FiscalActivity:
    a = db.get(FiscalActivity, aid)
    if not a or a.tenant_id != p.tenant.id:
        raise HTTPException(404, "Actividad no encontrada")
    return a


@router.put("/settings/hacienda/activities/{aid}")
def activity_update(aid: int, data: ActivityIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    a = _activity(db, p, aid)
    if a.is_default and (not data.is_default or not data.active):
        raise HTTPException(409, "Marque otra actividad por defecto primero")
    if data.code != a.code and db.scalar(select(FiscalActivity).where(FiscalActivity.tenant_id == p.tenant.id, FiscalActivity.code == data.code)):
        raise HTTPException(409, "Esa actividad ya está en la lista")
    antes = snapshot(a)
    a.code, a.description, a.is_default, a.active = data.code, data.description, data.is_default and data.active, data.active
    _apply_default(db, p, a)
    _sync_legacy_codes(db, p)
    audit_change(db, p, "update", "fiscal_activity", a.id, antes, snapshot(a))
    db.commit()
    return _activity_out(a)


@router.delete("/settings/hacienda/activities/{aid}")
def activity_delete(aid: int, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    a = _activity(db, p, aid)
    if a.is_default:
        raise HTTPException(409, "No se puede quitar la actividad por defecto: marque otra primero")
    a.active = False  # desactiva; no borra (las respuestas viejas la referencian)
    _sync_legacy_codes(db, p)
    audit(db, p.tenant.id, p.user.id, "deactivate", "fiscal_activity", a.id, {"code": a.code}, ip=p.ip)
    db.commit()
    return _activity_out(a)


@router.get("/settings/hacienda/activities/lookup")
def activity_lookup(p: Principal = Depends(require("settings", "configurar"))):
    ced = xml44.digits(p.tenant.tax_id)
    if not ced:
        raise HTTPException(422, "Configure la cédula de la empresa primero")
    try:
        return lookup_activities(ced)
    except HaciendaUnavailable as e:
        raise HTTPException(502, f"No se pudo consultar Hacienda: {e}") from e


# ---------------------------------------------------------------- credenciales
def _row(db: Session, tenant_id: int, env: str) -> FiscalCredential | None:
    return db.scalar(select(FiscalCredential).where(FiscalCredential.tenant_id == tenant_id, FiscalCredential.environment == env))


def _cedula_mismatch(serial_attr: str | None, tax_id: str | None) -> bool:
    if not serial_attr:
        return False
    d_cert, d_emp = xml44.digits(serial_attr), xml44.digits(tax_id)
    if not d_cert or not d_emp:
        return False
    return int(d_cert) != int(d_emp)


@router.put("/settings/hacienda/credentials/{environment}")
async def put_credentials(
    environment: str,
    admin_password: str = Form(""),
    atv_user: str = Form(""),
    atv_password: str = Form(""),
    pin: str = Form(""),
    allow_other_id: bool = Form(False),
    p12: UploadFile | None = File(None),
    p: Principal = Depends(require("settings", "configurar")),
    db: Session = Depends(get_db),
):
    env = _env(environment)
    require_reauth(p, admin_password or None)
    if not secretbox.available():
        raise HTTPException(503, "Falta configurar FISCAL_KEYS en el servidor")
    if len(pin) > 64:
        raise HTTPException(422, "El PIN admite hasta 64 caracteres")
    if len(atv_user) > 200 or len(atv_password) > 200:
        raise HTTPException(422, "Usuario o contraseña demasiado largos")
    p12_bytes = None
    if p12 is not None and (p12.filename or ""):
        p12_bytes = await p12.read(MAX_P12 + 1)  # en memoria: nunca a disco ni a multimedia
        if len(p12_bytes) > MAX_P12:
            raise HTTPException(413, "El certificado .p12 supera 64 KB")
        if not p12_bytes:
            p12_bytes = None
    row = _row(db, p.tenant.id, env)
    aad = lambda f: secretbox.aad_for(p.tenant.id, env, f)  # noqa: E731
    info = None
    if p12_bytes is not None or pin:
        try:
            final_p12 = p12_bytes if p12_bytes is not None else (secretbox.unseal(row.p12_enc, aad("p12")) if row and row.p12_enc else None)
            final_pin = pin or (secretbox.unseal_text(row.p12_pin_enc, aad("p12_pin")) if row and row.p12_pin_enc else None)
        except secretbox.SecretUnavailable as e:
            raise HTTPException(422, f"El certificado guardado es ilegible con la llave actual: cargue el .p12 y el PIN juntos ({e})") from e
        if final_p12 is None:
            raise HTTPException(422, "Falta el certificado .p12")
        if final_pin is None:
            raise HTTPException(422, "Falta el PIN del certificado")
        try:
            info = load_p12(final_p12, final_pin)
        except CertError as e:
            raise HTTPException(422, str(e)) from e
        if not allow_other_id and _cedula_mismatch(info.serial_number_attr, p.tenant.tax_id):
            raise HTTPException(422, "El certificado parece ser de otra cédula")
    if row is None:
        row = FiscalCredential(tenant_id=p.tenant.id, environment=env)
        db.add(row)
    fields = []
    if atv_user.strip():
        row.atv_user_enc = secretbox.seal_text(atv_user.strip(), aad("atv_user"))
        fields.append("atv_user")
    if atv_password:
        row.atv_password_enc = secretbox.seal_text(atv_password, aad("atv_password"))
        fields.append("atv_password")
    if p12_bytes is not None:
        row.p12_enc = secretbox.seal(p12_bytes, aad("p12"))
        fields.append("p12")
    if pin:
        row.p12_pin_enc = secretbox.seal_text(pin, aad("p12_pin"))
        fields.append("p12_pin")
    if info is not None:
        row.cert_subject, row.cert_issuer, row.cert_serial = info.subject, info.issuer, info.serial
        row.cert_not_before, row.cert_not_after, row.cert_sha256, row.cert_key_bits = info.not_before, info.not_after, info.sha256, info.key_bits
    if fields:
        row.key_id = secretbox.primary_kid()
        row.updated_by = p.user.id
        row.last_test_at, row.last_test_ok, row.last_test_detail = None, None, None  # credenciales nuevas: probar de nuevo
    audit(db, p.tenant.id, p.user.id, "hacienda_credentials", "fiscal_credential", None, {"environment": env, "fields": fields, "cert_sha256": row.cert_sha256}, ip=p.ip)
    db.commit()
    return hc.credentials_block(db, p.tenant, env)


class AdminPasswordIn(BaseModel):
    admin_password: str | None = None


@router.delete("/settings/hacienda/credentials/{environment}")
def delete_credentials(environment: str, data: AdminPasswordIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    env = _env(environment)
    require_reauth(p, data.admin_password)
    if env == "produccion" and hc.production_on(db, p.tenant):
        raise HTTPException(409, "Desactive producción primero")
    row = _row(db, p.tenant.id, env)
    if row is not None:
        db.delete(row)
    audit(db, p.tenant.id, p.user.id, "hacienda_credentials_delete", "fiscal_credential", None, {"environment": env}, ip=p.ip)
    db.commit()
    return hc.credentials_block(db, p.tenant, env)


@router.post("/settings/hacienda/credentials/{environment}/test")
def test_credentials(environment: str, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    env = _env(environment)
    hacienda_test_limiter.hit(f"{p.tenant.id}:{env}")
    creds, err = hc.credentials_status(db, p.tenant, env)
    ok, detail = False, err or "Faltan credenciales"
    if creds:
        try:
            load_p12(creds.p12, creds.pin)
            instance = None
            if env == "produccion":
                allowed, why = production_keys(instance_fingerprint())  # solo token y logout: no se envia ningun comprobante
                if not allowed:
                    raise ProductionBlocked(why or "El servidor no permite producción")
                instance = instance_fingerprint()
            client = HaciendaClient(env, creds.username, creds.password, tenant_id=p.tenant.id, production_instance=instance)
            client.token(force=True)
            client.logout()
            ok, detail = True, f"Conectado al ambiente de {'pruebas' if env == 'pruebas' else 'producción'} de Hacienda"
        except CertError as e:
            detail = f"Certificado: {e}"
        except (HaciendaAuthError, HaciendaUnavailable, ProductionBlocked) as e:
            detail = str(e)
    row = _row(db, p.tenant.id, env)
    if row is not None:
        row.last_test_at, row.last_test_ok, row.last_test_detail = datetime.now(UTC), ok, detail[:300]
    elif creds is not None:  # credenciales de entorno: se guarda el resultado de la prueba en una fila vacia
        row = FiscalCredential(tenant_id=p.tenant.id, environment=env, last_test_at=datetime.now(UTC), last_test_ok=ok, last_test_detail=detail[:300])
        db.add(row)
    audit(db, p.tenant.id, p.user.id, "hacienda_test", "fiscal_credential", None, {"environment": env, "ok": ok}, ip=p.ip)
    db.commit()
    return {"ok": ok, "detail": detail}


# ---------------------------------------------------------------- consecutivos y produccion
@router.post("/settings/hacienda/consecutives/confirm")
def confirm_consecutives(data: AdminPasswordIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    require_reauth(p, data.admin_password)
    prof = hc.profile(db, p.tenant)
    prof.consecutives_confirmed_at, prof.consecutives_confirmed_by = datetime.now(UTC), p.user.id
    audit(db, p.tenant.id, p.user.id, "hacienda_consecutives_confirm", "tenant", p.tenant.id, {c["code"]: c["current"] for c in hc.consecutives_block(db, p.tenant)}, ip=p.ip)
    db.commit()
    return hacienda_state(db, p)


class ProductionIn(BaseModel):
    enabled: bool
    admin_password: str | None = None
    confirm_text: str | None = None


@router.post("/settings/hacienda/production")
def production(data: ProductionIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    require_reauth(p, data.admin_password)
    prof = hc.profile(db, p.tenant)
    if not data.enabled:
        hc.turn_off_production(db, p.tenant, p.user.id, ip=p.ip, reason="interruptor")
        db.commit()
        return hacienda_state(db, p)
    if (data.confirm_text or "").strip().upper() not in ("PRODUCCIÓN", "PRODUCCION"):
        raise HTTPException(422, "Escriba PRODUCCIÓN para confirmar")
    falta = [c for c in hc.readiness(db, p.tenant, "produccion") if not c["ok"]]
    if falta:
        raise HTTPException(409, "No se puede activar producción. Falta: " + "; ".join(c["detail"] for c in falta))
    st = dict(p.tenant.settings or {})
    st["einvoice_provider"] = "hacienda"
    p.tenant.settings = st
    flag_modified(p.tenant, "settings")
    prof.environment = "produccion"
    prof.production_enabled_at, prof.production_enabled_by = datetime.now(UTC), p.user.id
    prof.production_instance = instance_fingerprint()
    audit(db, p.tenant.id, p.user.id, "hacienda_production_on", "tenant", p.tenant.id, {"huella": prof.production_instance}, ip=p.ip)
    db.commit()
    return hacienda_state(db, p)
