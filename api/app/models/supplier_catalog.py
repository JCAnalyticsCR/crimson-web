"""Catalogo de proveedores, separado del catalogo propio (Andres: "de una manera categorizada diferente para no
mezclarlos con lo que nosotros tenemos localmente").

SupplierItem es lo que el proveedor ofrece (su codigo, su descripcion, su existencia, su precio = nuestro costo).
Product es lo que Crimson vende. Se ligan con product_id cuando alguien lo confirma (o cuando el codigo del
proveedor / el modelo coinciden exacto). Nunca se confunde el modelo universal (DS-2CD1047G3-LIU) con el SKU
del proveedor (SG2104): el primero esta en model, el segundo en code.
"""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from ..core.db import Base
from .base import TenantMixin, TimestampMixin


class SupplierItem(TenantMixin, TimestampMixin, Base):
    __tablename__ = "supplier_item"
    __table_args__ = (UniqueConstraint("tenant_id", "supplier_id", "code", name="uq_supplier_item_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("supplier.id", name="fk_supplier_item_supplier"), index=True)
    code: Mapped[str] = mapped_column(String(60))  # codigo del proveedor (SKU)
    name: Mapped[str] = mapped_column(String(300))  # primera linea de la descripcion
    description: Mapped[str | None] = mapped_column(Text)  # descripcion completa (con especificaciones)
    brand: Mapped[str | None] = mapped_column(String(60))
    model: Mapped[str | None] = mapped_column(String(80))  # modelo del fabricante, extraido de la descripcion
    model_key: Mapped[str | None] = mapped_column(String(80), index=True)  # modelo normalizado para casar
    category: Mapped[str | None] = mapped_column(String(120))  # categoria del proveedor (no la nuestra)
    subcategory: Mapped[str | None] = mapped_column(String(120))
    stock: Mapped[int | None] = mapped_column(Integer)  # existencia que reporta el proveedor
    cost: Mapped[float | None] = mapped_column(Numeric(14, 4))  # precio del proveedor = costo para Crimson
    prev_cost: Mapped[float | None] = mapped_column(Numeric(14, 4))  # costo de la lista anterior (si cambio)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    tax_rate: Mapped[float | None] = mapped_column(Numeric(6, 3))  # IVA si la lista lo trae
    source: Mapped[str] = mapped_column(String(10), default="excel")  # excel | api | manual
    source_file: Mapped[str | None] = mapped_column(String(200))
    list_date: Mapped[date | None] = mapped_column(Date)  # fecha de la lista de donde salio el dato
    # cuando cambio DE VERDAD la existencia o el costo (o cuando entro el item). list_date / supplier_updated_at dicen
    # cuando se consulto; esto dice desde cuando el dato es el mismo (pedido 21/10: "actualizado hace 3 h" confundia)
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # cuando la sincronizacion de Eurocomp completo (una sola vez por vinculo) el CABYS y las fotos del producto
    # vinculado si le faltaban. Vinculado despues (linked_at > enriched_at) -> se completa en la corrida siguiente
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSON)  # la fila tal como vino
    # vinculo con el catalogo propio: metodo sku | modelo | nombre | manual | creado
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id", name="fk_supplier_item_product", ondelete="SET NULL"), index=True)
    link_method: Mapped[str | None] = mapped_column(String(12))
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    linked_by: Mapped[int | None] = mapped_column(Integer)  # sin FK: la bitacora guarda quien fue aunque se borre
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())  # False = ya no viene en la lista


class SupplierListImport(TenantMixin, Base):
    """Cada lista importada (o sincronizacion por API): fecha de la lista, archivo y resumen. Historial por proveedor."""

    __tablename__ = "supplier_list_import"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("supplier.id", name="fk_supplier_list_import_supplier"), index=True)
    list_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(10), default="excel")
    source_file: Mapped[str | None] = mapped_column(String(200))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    imported_by: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[dict | None] = mapped_column(JSON)
