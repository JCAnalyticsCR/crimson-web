"""Mi Tienda -> Catalogo (O4-04): el catalogo web unificado (cuadricula con filtros de calidad) y el cajon con todo lo
web de un producto. La logica vive en services/store_catalog (la comparten los endpoints viejos)."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import Principal, require
from ..models import Product
from ..services import store_catalog as scat

router = APIRouter(tags=["tienda"])


@router.get("/store/catalog")
def catalog_list(
    q: str | None = Query(None, max_length=120),
    categoria: int | None = None,
    marca: str | None = Query(None, max_length=60),
    estado: str | None = Query(None, pattern="^(en_bodega|bajo_pedido|consultar|agotado|servicio)$"),
    publicado: str = Query("si", pattern="^(si|no|todos)$"),
    falta: str | None = Query(None, pattern=f"^({'|'.join(scat.FALTAS)})$"),
    pagina: int = Query(1, ge=1, le=10000),
    p: Principal = Depends(require("catalog", "editar")),
    db: Session = Depends(get_db),
):
    """Catalogo web paginado en el servidor (48 por pagina) con el numero de cada filtro de calidad."""
    return scat.listing(db, p, q=q, category_id=categoria, brand=marca, estado=estado, publicado=publicado, falta=falta, page=pagina)


def _own(db: Session, p: Principal, pid: int) -> Product:
    pr = db.get(Product, pid)
    if not pr or pr.tenant_id != p.tenant.id or not pr.active:
        raise HTTPException(404, "Producto no encontrado")
    return pr


@router.get("/store/catalog/{pid}")
def catalog_item(pid: int, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    return scat.detail(db, p, _own(db, p, pid))


class ImageIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str = Field(max_length=500, pattern=r"^(/[^\s]*|https://[^\s]+)$")


class CatalogPatch(BaseModel):
    """Lo web de un producto. Nunca el nombre fiscal ni el codigo; tampoco un precio de comparacion escrito a mano
    (O4-05: una oferta solo se muestra con historial propio)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    show_on_web: bool | None = None
    web_featured: bool | None = None
    web_price: Decimal | None = Field(None, ge=0, le=Decimal("999999999"))
    description_store: str | None = Field(None, max_length=5000)
    images: list[ImageIn] | None = Field(None, max_length=20)
    specs: dict[str, str] | None = None
    datasheet_url: str | None = Field(None, max_length=500, pattern=r"^(|/[^\s]*|https://[^\s]+)$")
    category_id: int | None = None
    brand: str | None = Field(None, max_length=60)

    @field_validator("specs")
    @classmethod
    def _specs(cls, v):
        if v is None:
            return v
        if len(v) > 30:
            raise ValueError("Máximo 30 especificaciones")
        return {str(k).strip()[:40]: str(x).strip()[:120] for k, x in v.items() if str(k).strip() and str(x).strip()}


@router.patch("/store/catalog/{pid}")
def catalog_patch(pid: int, data: CatalogPatch, p: Principal = Depends(require("catalog", "editar")), db: Session = Depends(get_db)):
    """Un solo PATCH para todo lo web del producto: permisos por campo (publicar = catalog.publicar; precio web =
    catalog.precios), guardian de margen, bitacora y una marca del catalogo."""
    pr = _own(db, p, pid)
    vals = data.model_dump(exclude_unset=True)
    if "images" in vals:  # la primera es la principal, como en la tienda
        vals["images"] = [{"url": i["url"], **({"main": True} if k == 0 else {})} for k, i in enumerate(vals["images"] or [])]
    for k in ("show_on_web", "web_featured"):
        if k in vals and vals[k] is None:
            del vals[k]
    scat.apply(db, p, pr, vals, desde="mi_tienda")
    db.commit()
    db.refresh(pr)
    return scat.detail(db, p, pr)

