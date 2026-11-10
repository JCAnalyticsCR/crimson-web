from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import ArchiveMixin, TenantMixin, TimestampMixin


class Tax(TenantMixin, Base):
    """Impuesto aplicable por linea: IVA 13/4/2/1/0 (codigo v4.4 + tarifa)."""

    __tablename__ = "tax"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    code: Mapped[str] = mapped_column(String(4), default="01")  # 01 = IVA
    rate_code: Mapped[str] = mapped_column(String(4), default="08")  # 08 = tarifa general 13%
    rate: Mapped[float] = mapped_column(Numeric(6, 3), default=13)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Category(TenantMixin, Base):
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"))
    show_on_web: Mapped[bool] = mapped_column(Boolean, default=True)
    # margen sobre la venta de la categoria (Catalogo -> Margenes). Lo heredan subcategorias y productos sin margen
    # propio: producto -> categoria mas cercana -> general (Ajustes). Solo lo ve quien ve costos.
    margin_pct: Mapped[float | None] = mapped_column(Numeric(6, 2))


class Supplier(ArchiveMixin, TenantMixin, Base):
    __tablename__ = "supplier"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    tax_id: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))


class Product(ArchiveMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "product"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_product_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    code: Mapped[str] = mapped_column(String(60))
    item_type: Mapped[str] = mapped_column(String(10), default="producto")  # producto | servicio
    price: Mapped[float] = mapped_column(Numeric(14, 5), default=0)  # precio de venta (sin IVA)
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    cost: Mapped[float | None] = mapped_column(Numeric(14, 4))  # costo del proveedor
    cost_currency: Mapped[str] = mapped_column(String(3), default="USD")
    margin_pct: Mapped[float | None] = mapped_column(Numeric(6, 2))  # margen con el que se calculo el precio
    brand: Mapped[str | None] = mapped_column(String(60), index=True)
    model: Mapped[str | None] = mapped_column(String(80))  # DS-2CD1047G3-LIU
    supplier_sku: Mapped[str | None] = mapped_column(String(60))
    supplier_stock: Mapped[int | None] = mapped_column(Integer)  # existencias del proveedor (bajo pedido)
    supplier_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    weight_kg: Mapped[float | None] = mapped_column(Numeric(10, 3))
    show_on_web: Mapped[bool] = mapped_column(Boolean, default=False)
    description_invoice: Mapped[str | None] = mapped_column(Text)  # larga: va a documentos
    description_store: Mapped[str | None] = mapped_column(Text)  # corta: links y tienda
    images: Mapped[list] = mapped_column(JSON, default=list)  # [{url, main}]
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id"))
    registration_number: Mapped[str | None] = mapped_column(String(80))
    cabys_code: Mapped[str | None] = mapped_column(String(13))
    cabys_description: Mapped[str | None] = mapped_column(String(300))
    # quien confirmo el CABYS y cuando (pantalla "Completar CABYS"; el detalle queda en audit_log)
    cabys_set_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_product_cabys_set_by"))
    cabys_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tariff_code: Mapped[str | None] = mapped_column(String(20))  # partida arancelaria
    unit: Mapped[str] = mapped_column(String(10), default="Unid")
    min_stock: Mapped[int] = mapped_column(Integer, default=0)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"))
    # bodega de origen por defecto: de que inventario sale el producto (Andres la pidio en el importador)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouse.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # unidades con numero de serie (camaras, NVR): cada una se sigue en stock_unit. Opcional.
    serialized: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # tienda: precio propio de la web (sin IVA, moneda del producto; vacio = el precio de siempre) y destacado
    web_price: Mapped[float | None] = mapped_column(Numeric(14, 5))
    web_featured: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # vitrina (tienda propia): precio anterior sin IVA (tachado si es mayor), ficha tecnica PDF y especificaciones a mano
    compare_price: Mapped[float | None] = mapped_column(Numeric(14, 5))
    datasheet_url: Mapped[str | None] = mapped_column(String(500))
    specs: Mapped[dict | None] = mapped_column(JSON)
    # guardian de margen (services/margin_guard): margin_hold = fuera de venta en la tienda ("Consultar") por quedar
    # bajo el margen minimo; margin_flagged_at = desde cuando espera en Aprobaciones -> "Precios por revisar";
    # margin_info = el ultimo calculo (margen, costo, sugerido, dudoso) y el costo de referencia para detectar saltos
    margin_hold: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    margin_flagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    margin_info: Mapped[dict | None] = mapped_column(JSON)
    # tienda (O0-07, aun no se muestra): web_updated_at = ultima vez que cambio algo que el cliente ve (nombre, precio,
    # fotos, descripcion, publicacion); la sincronizacion de costo o existencia NO lo toca (services/price_history).
    # hidden_since = desde cuando un producto publicado no sale en la tienda (lo fija y lo limpia el rearmado del
    # snapshot, services/store_snapshot.rebuild). Nulas = sin dato.
    web_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    taxes: Mapped[list["ProductTax"]] = relationship(cascade="all, delete-orphan")


class ProductTax(Base):
    __tablename__ = "product_tax"
    __table_args__ = (UniqueConstraint("product_id", "tax_id", name="uq_product_tax"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))
    tax_id: Mapped[int] = mapped_column(ForeignKey("tax.id"))
    tax: Mapped[Tax] = relationship()


class ProductPriceHistory(TenantMixin, Base):
    """Historial de precios (O0-07): una fila cada vez que cambia el precio de venta, el precio web o el IVA de un
    producto. Solo registra: nunca cambia un precio. Lo escribe services/price_history al confirmar la transaccion,
    cualquiera sea el camino (ficha, lista, ajuste en bloque, importacion, sincronizacion, guardian de margen).
    La migracion deja una fila "inicial" por producto con el precio que tenia ese dia (antes no hay historial).

    price_net: Product.price (sin IVA). web_price_net: Product.web_price (None = la tienda usa price).
    price_final: lo que paga el cliente en la tienda, con IVA, en la moneda del producto (sin tipo de cambio).
    source: manual | sync | lote | inicial. changed_by: usuario (entero sin FK, como los *_by del archivo)."""

    __tablename__ = "product_price_history"
    __table_args__ = (Index("ix_product_price_history_product_valid", "product_id", "valid_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))
    price_net: Mapped[float] = mapped_column(Numeric(14, 5))
    web_price_net: Mapped[float | None] = mapped_column(Numeric(14, 5))
    currency: Mapped[str] = mapped_column(String(3))
    tax_rate: Mapped[float] = mapped_column(Numeric(6, 3))
    price_final: Mapped[float] = mapped_column(Numeric(14, 2))
    source: Mapped[str] = mapped_column(String(10))
    changed_by: Mapped[int | None] = mapped_column(Integer)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
