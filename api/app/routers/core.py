"""Health, empresa (tenant), tipos de cambio."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.db import get_db
from ..core.deps import Principal, get_principal, require
from ..models import ExchangeRate
from ..schemas.core import ExchangeRateIn, ExchangeRateOut, TenantOut, TenantUpdate

router = APIRouter(tags=["core"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "app": settings.app_name, "env": settings.env}


@router.get("/health/deep")
def health_deep(request: Request, db: Session = Depends(get_db), x_health_token: str | None = Header(default=None)):
    """Salud profunda para el monitor externo (plan 1.8): latido del beat y del worker, correo del worker, Eurocomp,
    correo fallido, tipo de cambio, ENV, ONVO y cola "Por emitir"; 503 si algo critico falla.
    Publico: solo el estado (el monitor mira el codigo). El detalle de cada revision, con X-Health-Token igual a la
    variable HEALTH_TOKEN (sin ella, nunca). Limite por IP y resultado guardado unos segundos: cada pasada cuesta."""
    import hmac
    import os

    from fastapi.responses import JSONResponse

    from ..core.ratelimit import health_limiter
    from ..services import salud as svc
    from .auth import client_ip

    health_limiter.hit(f"deep:{client_ip(request)}")
    cache = getattr(request.app.state, "salud_deep", None)
    if cache is None:
        cache = request.app.state.salud_deep = svc.DeepCache(svc.DEEP_CACHE_SECONDS)
    out = cache.get(lambda: svc.deep(db))
    token = (os.getenv("HEALTH_TOKEN") or "").strip()
    detail = bool(token and x_health_token and hmac.compare_digest(x_health_token.encode(), token.encode()))
    body = out if detail else svc.public_view(out)
    return JSONResponse(body, status_code=503 if out["status"] == "falla" else 200, headers={"Cache-Control": "no-store"})


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


@router.get("/fx/today", response_model=ExchangeRateOut | None)
def fx_today(currency: str = "USD", _: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return db.scalar(select(ExchangeRate).where(ExchangeRate.currency == currency, ExchangeRate.date <= date.today()).order_by(ExchangeRate.date.desc()))


@router.put("/fx", response_model=ExchangeRateOut)
def fx_upsert(data: ExchangeRateIn, _: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    fx = db.scalar(select(ExchangeRate).where(ExchangeRate.date == data.date, ExchangeRate.currency == data.currency))
    if fx is None:
        fx = ExchangeRate(date=data.date, currency=data.currency)
        db.add(fx)
    fx.sell, fx.buy, fx.source, fx.note = data.sell, data.buy, "manual", data.note
    db.commit()
    db.refresh(fx)
    return fx


def save_bccr_today(db: Session) -> ExchangeRate:
    """Consulta el BCCR (API SDDE) y guarda venta/compra de hoy. Compartido con el worker diario."""
    from ..providers.fx.bccr import fetch_today

    sell, buy = fetch_today()
    fx = db.scalar(select(ExchangeRate).where(ExchangeRate.date == date.today(), ExchangeRate.currency == "USD"))
    if fx is None:
        fx = ExchangeRate(date=date.today(), currency="USD")
        db.add(fx)
    fx.sell, fx.buy, fx.source, fx.note = sell, buy, "bccr", "API SDDE del BCCR"
    db.commit()
    db.refresh(fx)
    return fx


@router.post("/fx/bccr", response_model=ExchangeRateOut)
def fx_bccr(_: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    """Actualiza ya el tipo de cambio desde el BCCR (el worker lo hace solo cada dia a las 6:15)."""
    from fastapi import HTTPException

    from ..providers.fx.bccr import BccrError

    try:
        return save_bccr_today(db)
    except BccrError as e:
        raise HTTPException(422 if "Falta" in str(e) else 502, str(e)) from e
    except Exception as e:  # noqa: BLE001 - red caida, BCCR fuera de servicio
        raise HTTPException(502, f"No se pudo consultar el BCCR: {e}") from e
