"""Bandeja de Aprobaciones (ver services/approvals.py). Casi todo es de lectura: aprobar se hace en cada pantalla.
La excepcion es "Precios por revisar" (guardian de margen, plan 1.12): el precio sugerido se aplica aqui con un clic."""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..core.ratelimit import margin_run_limiter
from ..models import Product, Tenant
from ..services import approvals as svc
from ..services import margin_guard

router = APIRouter(tags=["aprobaciones"])


@router.get("/approvals")
def approvals(
    limit: int = Query(20, ge=1, le=500),
    section: str | None = Query(None, max_length=60),
    p: Principal = Depends(require("approvals", "ver")),
    db: Session = Depends(get_db),
):
    """Secciones que este usuario puede aprobar (aunque esten en 0), lo mas antiguo primero. Toda la bandeja trae
    como maximo 50 pendientes por seccion; `section` trae solo esa con hasta 500 ("Ver todos")."""
    return svc.full(db, p, limit if section else min(limit, 50), only=section)


@router.get("/approvals/summary")
def approvals_summary(p: Principal = Depends(require("approvals", "ver")), db: Session = Depends(get_db)):
    """Solo conteos, para el numero del menu."""
    return svc.summary(db, p)


def _pricer(p: Principal) -> Principal:
    # el precio sugerido deja deducir el costo: solo quien ve costos (y puede editar el catalogo)
    if not p.sees_costs:
        raise HTTPException(403, "Solo quien ve costos revisa precios por margen")
    return p


class ApplyIn(BaseModel):
    price: Decimal = Field(gt=0)  # el precio sugerido que la persona vio y confirmo (sin IVA, moneda del producto)


@router.post("/approvals/prices/{pid}/apply")
def apply_price(pid: int, data: ApplyIn, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """ "Aplicar": el precio que la persona vio pasa a ser el de la tienda (en lo que esta bajo el minimo) y el producto
    vuelve a la venta. Se recalcula antes: si ya no esta en revision o el sugerido de hoy es otro, 409 con el motivo (y
    la bandeja queda con el sugerido nuevo); nunca aplica un monto distinto del confirmado. Queda en la bitacora."""
    _pricer(p)
    prod = db.get(Product, pid)
    if not prod or prod.tenant_id != p.tenant.id:
        raise HTTPException(404, "Producto no encontrado")
    try:
        out = margin_guard.apply(db, db.get(Tenant, p.tenant.id), prod, p.user.id, ip=p.ip, expected=data.price)
    except margin_guard.Stale as e:
        db.commit()  # lo recalculado (sugerido nuevo o la liberacion) queda para la proxima vista
        raise HTTPException(409, str(e)) from None
    db.commit()
    return out


class RunIn(BaseModel):
    # una persona verifico el tipo de cambio y los costos: destraba el freno y saca de venta todo lo que este bajo
    # el minimo
    confirmar: bool = False


@router.post("/approvals/prices/run")
def run_guard(data: RunIn | None = None, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """Revisar ya los margenes de la tienda (lo hace solo despues de cada refresco de Eurocomp, de cada lista y de cada
    tasa nueva). {"confirmar": true} = "Sacar de venta todo" cuando la revision quedo frenada."""
    _pricer(p)
    margin_run_limiter.hit(f"t{p.tenant.id}")
    out = margin_guard.run(db, db.get(Tenant, p.tenant.id), reason="revisión manual", confirm=bool(data and data.confirmar))
    db.commit()
    return out
