"""Mi Tienda por secciones (Ola 4): resumen «Lista para vender» (O4-03) e historial seguro con «Restaurar» (O4-02).

Rutas del panel (con sesion). Lo publico de la tienda sigue en store.py y store_showcase.py."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require, require_one_of
from ..models import Tenant
from ..services import store_health, store_history

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


# ---------- O4-02: historial seguro ----------
@router.get("/store/history")
def store_history_get(p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    """Ultimos 20 guardados de Mi Tienda: quien, cuando, que campos y cuales se pueden restaurar (sin valores)."""
    return store_history.history(db, db.get(Tenant, p.tenant.id))


@router.post("/store/history/{hid}/restore")
def store_history_restore(hid: int, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    """Vuelve a poner lo de antes de ese guardado, solo en los campos de la lista blanca (nunca publicacion, dominio,
    tipo de tienda, moneda ni cuentas). Queda en el historial y en la bitacora."""
    return store_history.restore(db, p, hid)
