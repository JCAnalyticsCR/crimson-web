"""Health, empresa (tenant), tipos de cambio."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
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


@router.get("/tenant", response_model=TenantOut)
def get_tenant(p: Principal = Depends(get_principal)):
    return p.tenant


@router.patch("/tenant", response_model=TenantOut)
def update_tenant(data: TenantUpdate, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    for k, v in data.model_dump(exclude_unset=True).items():
        if k == "settings" and v is not None:
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
