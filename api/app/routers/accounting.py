"""Contabilidad -> Tributario: panel del mes (IVA en colones, plazos, rechazos, estado de Hacienda) y la prorrata.
Sin costos de producto ni margenes (D10)."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..core.db import get_db
from ..core.deps import Principal, require
from ..services import reports as rp
from ..services.bitacora import audit_change

router = APIRouter(prefix="/accounting", tags=["contabilidad"])


@router.get("/tax-summary")
def tax_summary(month: str | None = None, p: Principal = Depends(require("accounting", "ver")), db: Session = Depends(get_db)):
    month = month or date.today().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "Mes inválido (AAAA-MM)")
    out = rp.tax_summary(db, p.tenant, month)
    db.commit()
    return out


class AccountingSettingsIn(BaseModel):
    iva_prorrata_pct: Decimal | None = Field(None, ge=0, le=100)


@router.put("/settings")
def put_settings(data: AccountingSettingsIn, p: Principal = Depends(require("accounting", "editar")), db: Session = Depends(get_db)):
    st = dict(p.tenant.settings or {})
    antes = {"iva_prorrata_pct": st.get("iva_prorrata_pct")}
    st["iva_prorrata_pct"] = float(data.iva_prorrata_pct) if data.iva_prorrata_pct is not None else None
    p.tenant.settings = st
    flag_modified(p.tenant, "settings")
    audit_change(db, p, "update", "tenant", p.tenant.id, antes, {"iva_prorrata_pct": st["iva_prorrata_pct"]}, reason="Prorrata del IVA")
    db.commit()
    return {"iva_prorrata_pct": st["iva_prorrata_pct"]}
