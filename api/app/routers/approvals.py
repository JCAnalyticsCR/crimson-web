"""Bandeja de Aprobaciones (ver services/approvals.py). Casi todo es de lectura: aprobar se hace en cada pantalla.
La excepcion es "Precios por revisar" (guardian de margen, plan 1.12): el precio sugerido se aplica aqui con un clic."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Product, Tenant
from ..services import approvals as svc
from ..services import margin_guard

router = APIRouter(tags=["aprobaciones"])


@router.get("/approvals")
def approvals(limit: int = Query(20, ge=1, le=50), p: Principal = Depends(require("approvals", "ver")), db: Session = Depends(get_db)):
    """Secciones que este usuario puede aprobar (aunque esten en 0), lo mas antiguo primero."""
    return svc.full(db, p, limit)


@router.get("/approvals/summary")
def approvals_summary(p: Principal = Depends(require("approvals", "ver")), db: Session = Depends(get_db)):
    """Solo conteos, para el numero del menu."""
    return svc.summary(db, p)


def _pricer(p: Principal) -> Principal:
    # el precio sugerido deja deducir el costo: solo quien ve costos (y puede editar el catalogo)
    if not p.sees_costs:
        raise HTTPException(403, "Solo quien ve costos revisa precios por margen")
    return p


@router.post("/approvals/prices/{pid}/apply")
def apply_price(pid: int, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """"Aplicar": el precio sugerido (recalculado ahora con el costo y el tipo de cambio vigentes) pasa a ser el
    precio de la tienda y el producto vuelve a la venta. Queda en la bitacora con el precio anterior."""
    _pricer(p)
    prod = db.get(Product, pid)
    if not prod or prod.tenant_id != p.tenant.id:
        raise HTTPException(404, "Producto no encontrado")
    out = margin_guard.apply(db, db.get(Tenant, p.tenant.id), prod, p.user.id, ip=p.ip)
    db.commit()
    return out


@router.post("/approvals/prices/run")
def run_guard(p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """Revisar ya los margenes de la tienda (lo hace solo despues de cada refresco de Eurocomp y de cada tasa nueva)."""
    _pricer(p)
    out = margin_guard.run(db, db.get(Tenant, p.tenant.id), reason="revisión manual")
    db.commit()
    return out
