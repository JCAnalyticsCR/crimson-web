"""Traspaso de cartera de un clic (ventas etapa 2): admin o jefe de ventas (crm.cartera).

Ver services/cartera.py: clientes, oportunidades abiertas y cotizaciones abiertas pasan a otro vendedor; las comisiones
ya devengadas o pendientes se quedan con el vendedor original. Primero la vista previa con los conteos.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..services import cartera

router = APIRouter(prefix="/cartera", tags=["crm"])
CARTERA = require("crm", "cartera")


@router.get("/sellers")
def sellers(p: Principal = Depends(CARTERA), db: Session = Depends(get_db)):
    """El equipo comercial activo: a quien se le puede dar la cuenta de un cliente o una cartera."""
    from ..services.store_orders import sales_team

    return sales_team(db, p.tenant.id)


@router.get("/sellers/{uid}")
def portfolio_preview(uid: int, p: Principal = Depends(CARTERA), db: Session = Depends(get_db)):
    """Lo que se movería: cuántos clientes, oportunidades abiertas y cotizaciones abiertas, y a quién se le puede pasar."""
    return cartera.preview(db, p, uid)


class TransferIn(BaseModel):
    to_user_id: int = Field(ge=1)


@router.post("/sellers/{uid}/transfer")
def portfolio_transfer(uid: int, data: TransferIn, p: Principal = Depends(CARTERA), db: Session = Depends(get_db)):
    out = cartera.transfer(db, p, uid, data.to_user_id)
    db.commit()
    return out
