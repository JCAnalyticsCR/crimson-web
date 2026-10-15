"""Configuracion de Hacienda por empresa: perfil del emisor, modo efectivo, credenciales y lista de verificacion.

Fuente unica del modo (D3): tenant.settings.einvoice_provider (none | sandbox | hacienda; alanube/gti = none) +
fiscal_profile.environment (pruebas | produccion). hacienda_mode() devuelve ninguno | simulado | pruebas | produccion,
y "produccion" solo si el interruptor del admin esta encendido Y las tres llaves del servidor se cumplen en el momento
(HACIENDA_PRODUCTION_ALLOWED, ENV=production y la huella de instancia guardada). Si no, "pruebas": nada sale a produccion.
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import secretbox
from ..core.config import settings
from ..models import BillingGroup, FiscalActivity, FiscalCredential, FiscalProfile, Tenant
from ..providers.hacienda.client import instance_fingerprint, production_keys
from ..providers.hacienda.provider import Credentials
from .sequences import FISCAL_CODES, ensure_fiscal_groups

log = logging.getLogger("crimson.hacienda")
ENVIRONMENTS = ("pruebas", "produccion")
FIELDS = ("atv_user", "atv_password", "p12", "p12_pin")
ACTIVITY_RE = re.compile(r"^[0-9A-Za-z.]{6}$")
__all__ = ["ensure_fiscal_groups", "instance_fingerprint"]


def profile(db: Session, tenant: Tenant) -> FiscalProfile:
    prof = db.scalar(select(FiscalProfile).where(FiscalProfile.tenant_id == tenant.id))
    if prof is None:
        d = "".join(ch for ch in (tenant.tax_id or "") if ch.isdigit())
        prof = FiscalProfile(tenant_id=tenant.id, id_type="01" if len(d) == 9 else "02", environment="pruebas", phone_country="506")
        db.add(prof)
        db.flush()
    return prof


def provider_setting(tenant: Tenant) -> str:
    return (tenant.settings or {}).get("einvoice_provider") or settings.einvoice_provider


def production_state(db: Session, tenant: Tenant) -> tuple[bool, str | None]:
    """(produccion efectiva, motivo si el interruptor esta encendido pero el servidor no la permite)."""
    if provider_setting(tenant) != "hacienda":
        return False, None
    prof = profile(db, tenant)
    if prof.environment != "produccion" or not prof.production_enabled_at:
        return False, None
    ok, why = production_keys(prof.production_instance)
    return ok, why


def hacienda_mode(db: Session, tenant: Tenant) -> str:
    prov = provider_setting(tenant)
    if prov == "hacienda":
        return "produccion" if production_state(db, tenant)[0] else "pruebas"
    if prov == "sandbox":
        return "simulado"
    return "ninguno"  # none, alanube, gti


def reception_mode(db: Session, tenant: Tenant) -> str:
    """Para responder a proveedores, "ninguno" y "simulado" son ambos simulado (nunca se envia nada)."""
    m = hacienda_mode(db, tenant)
    return "simulado" if m in ("ninguno", "simulado") else m


def production_on(db: Session, tenant: Tenant) -> bool:
    prof = profile(db, tenant)
    return provider_setting(tenant) == "hacienda" and prof.environment == "produccion" and prof.production_enabled_at is not None


def turn_off_production(db: Session, tenant: Tenant, user_id: int | None, ip: str | None = None, reason: str = "cambio de modo") -> bool:
    """Salir de produccion: inmediato y sin contrasena. Devuelve True si estaba encendida."""
    from .documents import audit

    prof = profile(db, tenant)
    was = prof.environment == "produccion" or prof.production_enabled_at is not None
    prof.environment = "pruebas"
    prof.production_enabled_at = None
    prof.production_enabled_by = None
    prof.production_instance = None
    if was:
        audit(db, tenant.id, user_id, "hacienda_production_off", "tenant", tenant.id, {"motivo": reason}, ip=ip)
    return was


# ---------------------------------------------------------------- credenciales
def _cred_row(db: Session, tenant_id: int, environment: str) -> FiscalCredential | None:
    return db.scalar(select(FiscalCredential).where(FiscalCredential.tenant_id == tenant_id, FiscalCredential.environment == environment))


def _row_has_any(c: FiscalCredential | None) -> bool:
    return bool(c and (c.atv_user_enc or c.atv_password_enc or c.p12_enc or c.p12_pin_enc))


def env_tenant_applies(db: Session, tenant: Tenant, slug: str | None) -> bool:
    """Credenciales/buzon de entorno: de UNA empresa (la del slug o, sin slug, la unica activa)."""
    if slug:
        return tenant.slug == slug
    ids = db.scalars(select(Tenant.id).where(Tenant.active).limit(2)).all()
    return ids == [tenant.id]


def _env_credentials(db: Session, tenant: Tenant, environment: str) -> Credentials | None:
    if environment != settings.hacienda_environment or not env_tenant_applies(db, tenant, settings.hacienda_env_tenant):
        return None
    vals = (settings.hacienda_username, settings.hacienda_password, settings.hacienda_p12_b64, settings.hacienda_p12_pin)
    if not all(v is not None and v.get_secret_value() for v in vals):
        return None
    try:
        p12 = base64.b64decode(settings.hacienda_p12_b64.get_secret_value())
    except Exception:  # noqa: BLE001
        log.warning("HACIENDA_P12_B64 no es base64 válido")
        return None
    return Credentials(environment, "entorno", settings.hacienda_username.get_secret_value(), settings.hacienda_password.get_secret_value(), p12, settings.hacienda_p12_pin.get_secret_value())


def env_credentials_present(db: Session, tenant: Tenant, environment: str) -> bool:
    return _env_credentials(db, tenant, environment) is not None


def credentials_status(db: Session, tenant: Tenant, environment: str) -> tuple[Credentials | None, str | None]:
    """(credenciales completas o None, error legible). Ajustes manda sobre entorno para su ambiente."""
    c = _cred_row(db, tenant.id, environment)
    if _row_has_any(c):
        if not (c.atv_user_enc and c.atv_password_enc and c.p12_enc and c.p12_pin_enc):
            return None, f"Faltan credenciales de Hacienda del ambiente {environment}"
        try:
            aad = lambda f: secretbox.aad_for(tenant.id, environment, f)  # noqa: E731
            user = secretbox.unseal_text(c.atv_user_enc, aad("atv_user"))
            pwd = secretbox.unseal_text(c.atv_password_enc, aad("atv_password"))
            p12 = secretbox.unseal(c.p12_enc, aad("p12"))
            pin = secretbox.unseal_text(c.p12_pin_enc, aad("p12_pin"))
        except secretbox.SecretUnavailable as e:
            return None, f"Credenciales ilegibles con la llave actual: vuelva a cargarlas ({e})"
        return Credentials(environment, "ajustes", user, pwd, p12, pin), None
    envc = _env_credentials(db, tenant, environment)
    if envc:
        return envc, None
    return None, f"Cargue las credenciales de Hacienda del ambiente {environment}"


def credentials(db: Session, tenant: Tenant, environment: str) -> Credentials | None:
    return credentials_status(db, tenant, environment)[0]


def cert_days_left(cred: FiscalCredential | None) -> int | None:
    if not cred or not cred.cert_not_after:
        return None
    na = cred.cert_not_after if cred.cert_not_after.tzinfo else cred.cert_not_after.replace(tzinfo=UTC)
    return (na - datetime.now(UTC)).days


def cert_cn(rfc4514: str | None) -> str | None:
    if not rfc4514:
        return None
    m = re.search(r"(?:^|,)CN=((?:\\,|[^,])+)", rfc4514)
    return m.group(1).replace("\\,", ",") if m else None


def credentials_block(db: Session, tenant: Tenant, environment: str) -> dict:
    """Bloque credentials[env] de GET /settings/hacienda. NUNCA usuario, contrasena, PIN, .p12 ni texto cifrado."""
    c = _cred_row(db, tenant.id, environment)
    if _row_has_any(c):
        source = "ajustes"
    elif env_credentials_present(db, tenant, environment):
        source = "entorno"
    else:
        source = "ninguna"
    readable = True
    if source == "ajustes":
        _, err = credentials_status(db, tenant, environment)
        readable = not (err and "ilegibles" in err)
    cert = None
    if c and c.cert_sha256:
        cert = {
            "subject_cn": cert_cn(c.cert_subject),
            "issuer_cn": cert_cn(c.cert_issuer),
            "serial": c.cert_serial,
            "not_after": c.cert_not_after,
            "days_left": cert_days_left(c),
            "sha256": c.cert_sha256,
        }
    env = source == "entorno"
    return {
        "source": source,
        "atv_user_set": env or bool(c and c.atv_user_enc),
        "atv_password_set": env or bool(c and c.atv_password_enc),
        "p12_set": env or bool(c and c.p12_enc),
        "pin_set": env or bool(c and c.p12_pin_enc),
        "cert": cert,
        "readable": readable,
        "last_test_at": c.last_test_at if c else None,
        "last_test_ok": c.last_test_ok if c else None,
        "last_test_detail": c.last_test_detail if c else None,
    }


# ---------------------------------------------------------------- lista de verificacion
def profile_missing(db: Session, tenant: Tenant) -> list[str]:
    """Lo que falta del perfil del emisor para emitir (texto legible por campo)."""
    prof = profile(db, tenant)
    falta = []
    if not prof.id_type:
        falta.append("el tipo de identificación")
    if not "".join(ch for ch in (tenant.tax_id or "") if ch.isdigit()):
        falta.append("la cédula")
    if not (tenant.legal_name or "").strip() or len((tenant.legal_name or "").strip()) < 5:
        falta.append("la razón social")
    if not prof.email:
        falta.append("el correo de facturación")
    if not prof.province:
        falta.append("la provincia")
    if not prof.canton:
        falta.append("el cantón")
    if not prof.district:
        falta.append("el distrito")
    if not prof.other_signs or len(prof.other_signs.strip()) < 5:
        falta.append("otras señas")
    if not default_activity(db, tenant.id):
        falta.append("una actividad económica por defecto")
    return falta


def default_activity(db: Session, tenant_id: int) -> FiscalActivity | None:
    return db.scalar(
        select(FiscalActivity).where(FiscalActivity.tenant_id == tenant_id, FiscalActivity.active, FiscalActivity.is_default).order_by(FiscalActivity.id)
    )


def active_activity(db: Session, tenant_id: int, code: str) -> FiscalActivity | None:
    return db.scalar(select(FiscalActivity).where(FiscalActivity.tenant_id == tenant_id, FiscalActivity.code == code, FiscalActivity.active))


def max_receptor_imported(tenant: Tenant) -> dict[str, int]:
    raw = ((tenant.settings or {}).get("fygaro_import") or {}).get("max_receptor") or {}
    return {k: int(v) for k, v in raw.items() if str(v).isdigit()}


def _within(dt: datetime | None, hours: int) -> bool:
    if not dt:
        return False
    dt = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return datetime.now(UTC) - dt <= timedelta(hours=hours)


def readiness(db: Session, tenant: Tenant, environment: str) -> list[dict]:
    prof = profile(db, tenant)
    checks: list[dict] = []

    def add(key: str, ok: bool, detail: str):
        checks.append({"key": key, "ok": bool(ok), "detail": detail})

    falta = profile_missing(db, tenant)
    perfil_falta = [f for f in falta if f != "una actividad económica por defecto"]
    add("perfil", not perfil_falta, "Identificación y ubicación completas" if not perfil_falta else "Falta " + ", ".join(perfil_falta))
    act = default_activity(db, tenant.id)
    add("actividad", act is not None, f"Actividad por defecto {act.code}" if act else "Falta una actividad económica por defecto")
    creds, err = credentials_status(db, tenant, environment)
    add("credenciales", creds is not None, f"Credenciales del ambiente {environment} cargadas" if creds else (err or "Faltan credenciales"))
    row = _cred_row(db, tenant.id, environment)
    days = cert_days_left(row)
    if creds and creds.source == "entorno" and days is None:
        try:
            from ..providers.hacienda.xades import load_p12

            days = load_p12(creds.p12, creds.pin).days_left
        except Exception:  # noqa: BLE001
            days = None
    add("certificado", days is not None and days > 7, f"Certificado vigente ({days} días)" if days is not None and days > 7 else "Certificado vencido, por vencer (≤ 7 días) o sin cargar")
    tested = bool(row and row.last_test_ok and _within(row.last_test_at, 24))
    add("conexion", tested, "Prueba de conexión correcta en las últimas 24 h" if tested else "Haga «Probar conexión» (debe estar en verde en las últimas 24 h)")
    conf = _within(prof.consecutives_confirmed_at, 24)
    add("consecutivos", conf, "Consecutivos confirmados en las últimas 24 h" if conf else "Confirme los consecutivos (la confirmación vale 24 h)")
    groups = ensure_fiscal_groups(db, tenant.id)
    imported = max_receptor_imported(tenant)
    bajos = [f"{c} (F{c} en {groups[f'F{c}'].current}, Fygaro llegó a {n})" for c, n in sorted(imported.items()) if f"F{c}" in groups and groups[f"F{c}"].current < n]
    add("fygaro", not bajos, "Consecutivos de aceptación al día con el histórico de Fygaro" if not bajos else "Suba los consecutivos " + "; ".join(bajos))
    if environment == "produccion":
        ok = settings.hacienda_production_allowed and settings.env == "production"
        add("servidor", ok, "El servidor permite producción" if ok else "El servidor no permite producción (HACIENDA_PRODUCTION_ALLOWED y ENV=production)")
    return checks


def consecutives_block(db: Session, tenant: Tenant) -> list[dict]:
    groups = ensure_fiscal_groups(db, tenant.id)
    imported = max_receptor_imported(tenant)
    out = []
    for code, label in FISCAL_CODES.items():
        g: BillingGroup = groups[f"F{code}"]
        out.append(
            {
                "code": code,
                "label": label,
                "group_id": g.id,
                "branch": g.branch,
                "terminal": g.terminal,
                "current": g.current,
                "next_preview": f"{g.branch} {g.terminal} {code} {g.current + 1:010d}",
                "fygaro_max": imported.get(code),
                "test_group_id": groups[f"T{code}"].id,
                "test_current": groups[f"T{code}"].current,
            }
        )
    return out


def ensure_profile_and_groups(db: Session, tenant: Tenant) -> FiscalProfile:
    prof = profile(db, tenant)
    ensure_fiscal_groups(db, tenant.id)
    return prof
