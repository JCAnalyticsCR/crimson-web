"""Health, empresa (tenant), tipos de cambio."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..core.deps import Principal, get_principal, require
from ..models import ExchangeRate
from ..schemas.core import ExchangeRateIn, ExchangeRateOut, FxTodayOut, TenantOut, TenantUpdate

router = APIRouter(tags=["core"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "app": settings.app_name, "env": settings.env}


@router.get("/tenant", response_model=TenantOut)
def get_tenant(p: Principal = Depends(get_principal)):
    return p.tenant


@router.patch("/tenant", response_model=TenantOut)
def update_tenant(data: TenantUpdate, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    from ..services import hacienda_config as hc

    # Hacienda (D2): esta ruta no entra a produccion ni cambia la cedula con produccion activa; cambiar el modo la apaga
    st_in = (data.settings or {}) if "settings" in data.model_fields_set else {}
    if "einvoice_provider" in st_in:
        if st_in["einvoice_provider"] not in ("none", "sandbox", "alanube", "gti"):
            raise HTTPException(422, "El modo fiscal se cambia en Ajustes → Hacienda")
        if st_in["einvoice_provider"] != hc.provider_setting(p.tenant):
            hc.turn_off_production(db, p.tenant, p.user.id, ip=p.ip, reason="PATCH /tenant")
    if "tax_id" in data.model_fields_set and (data.tax_id or "") != (p.tenant.tax_id or "") and hc.production_on(db, p.tenant):
        raise HTTPException(409, "Desactive producción primero")
    for k, v in data.model_dump(exclude_unset=True).items():
        if k == "settings" and v is not None:
            v = dict(v)
            if "cost_roles" in v:
                # "Quien ve costos" es un permiso: solo por PUT /settings, que lo valida y lo deja en la bitacora.
                # Si viene igual a lo guardado (alguien reenvia el dict completo) se ignora; si cambia, se rechaza.
                if list(v.pop("cost_roles") or []) != list((p.tenant.settings or {}).get("cost_roles") or []):
                    raise HTTPException(422, "Quién ve costos se cambia en Ajustes → Usuarios (queda en la bitácora)")
            p.tenant.settings = {**(p.tenant.settings or {}), **v}
        else:
            setattr(p.tenant, k, v)
    db.commit()
    db.refresh(p.tenant)
    return p.tenant


@router.get("/fx", response_model=list[ExchangeRateOut])
def fx_list(currency: str = "USD", limit: int = 30, _: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return db.scalars(select(ExchangeRate).where(ExchangeRate.currency == currency).order_by(ExchangeRate.date.desc()).limit(limit)).all()


@router.get("/fx/today", response_model=FxTodayOut | None)
def fx_today(currency: str = "USD", _: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """La tasa que usa el sistema (services/fx: la mas reciente) con su fecha, fuente y si esta vieja (mas de 2 dias
    habiles). null si no hay ninguna: los documentos en esa moneda no se pueden guardar hasta cargarla."""
    from ..services import fx as fxsvc

    return fxsvc.info(db, currency)


def _after_new_rate(db: Session, reason: str, tenant_id: int | None = None) -> None:
    """Tasa nueva -> el guardian de margen revisa la tienda (su propio commit; un error suyo no deshace la tasa).
    Desde el portal solo la empresa de quien la cargo: el request no escribe ni saca de venta en otras empresas (la
    tasa es global; las demas las revisa la red diaria del worker). Desde el worker (tenant_id None), todas."""
    from ..models import Tenant
    from ..services import margin_guard

    if tenant_id is None:
        margin_guard.run_all(db, reason=reason)
        return
    t = db.get(Tenant, tenant_id)
    if t is not None and t.active:
        margin_guard.run_safe(db, t, reason=reason)


@router.put("/fx", response_model=ExchangeRateOut)
def fx_upsert(data: ExchangeRateIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    from ..core.ratelimit import margin_run_limiter

    margin_run_limiter.hit(f"t{p.tenant.id}")  # cada tasa recorre la tienda entera de la empresa
    fx = db.scalar(select(ExchangeRate).where(ExchangeRate.date == data.date, ExchangeRate.currency == data.currency))
    if fx is None:
        fx = ExchangeRate(date=data.date, currency=data.currency)
        db.add(fx)
    fx.sell, fx.buy, fx.source, fx.note = data.sell, data.buy, "manual", data.note
    db.commit()
    db.refresh(fx)
    out = ExchangeRateOut.model_validate(fx)
    _after_new_rate(db, "tipo de cambio manual", p.tenant.id)
    return out


def save_bccr_today(db: Session, tenant_id: int | None = None) -> ExchangeRate:
    """Consulta el BCCR (API SDDE) y guarda venta/compra de hoy. Compartido con el worker diario (tenant_id None: el
    guardian revisa todas las empresas; desde el portal, solo la de quien lo pidio)."""
    from ..providers.fx.bccr import fetch_today

    sell, buy = fetch_today()
    fx = db.scalar(select(ExchangeRate).where(ExchangeRate.date == date.today(), ExchangeRate.currency == "USD"))
    if fx is None:
        fx = ExchangeRate(date=date.today(), currency="USD")
        db.add(fx)
    fx.sell, fx.buy, fx.source, fx.note = sell, buy, "bccr", "API SDDE del BCCR"
    db.commit()
    db.refresh(fx)
    _after_new_rate(db, "tipo de cambio del BCCR", tenant_id)
    db.refresh(fx)
    return fx


@router.post("/fx/bccr", response_model=ExchangeRateOut)
def fx_bccr(p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    """Actualiza ya el tipo de cambio desde el BCCR (el worker lo hace solo cada dia a las 6:15)."""
    from fastapi import HTTPException

    from ..core.ratelimit import margin_run_limiter
    from ..providers.fx.bccr import BccrError

    margin_run_limiter.hit(f"t{p.tenant.id}")
    try:
        return save_bccr_today(db, p.tenant.id)
    except BccrError as e:
        raise HTTPException(422 if "Falta" in str(e) else 502, str(e)) from e
    except Exception as e:  # noqa: BLE001 - red caida, BCCR fuera de servicio
        raise HTTPException(502, f"No se pudo consultar el BCCR: {e}") from e
