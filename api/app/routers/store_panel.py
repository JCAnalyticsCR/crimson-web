"""Mi Tienda por secciones (Ola 4): resumen «Lista para vender» (O4-03) e historial seguro con «Restaurar» (O4-02).

Rutas del panel (con sesion). Lo publico de la tienda sigue en store.py y store_showcase.py."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require_one_of
from ..models import Tenant
from ..services import store_health

router = APIRouter(tags=["tienda"])


@router.get("/store/health")
def store_health_get(
    response: Response,
    p: Principal = Depends(require_one_of(("settings", "configurar"), ("catalog", "editar"))),
    db: Session = Depends(get_db),
):
    """Resumen de Mi Tienda (solo lectura): indicadores y la lista «Lista para vender» con adonde corregir cada punto.
    Se recuerda 60 s por empresa y version del catalogo."""
    response.headers["Cache-Control"] = "private, max-age=60"
    return store_health.health(db, db.get(Tenant, p.tenant.id))

