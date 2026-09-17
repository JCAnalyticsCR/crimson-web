from sqlalchemy import JSON, Boolean, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin


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


class Supplier(TenantMixin, Base):
    __tablename__ = "supplier"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    tax_id: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))


class Product(TenantMixin, TimestampMixin, Base):
    __tablename__ = "product"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_product_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    code: Mapped[str] = mapped_column(String(60))
    item_type: Mapped[str] = mapped_column(String(10), default="producto")  # producto | servicio
    price: Mapped[float] = mapped_column(Numeric(14, 5), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    weight_kg: Mapped[float | None] = mapped_column(Numeric(10, 3))
    show_on_web: Mapped[bool] = mapped_column(Boolean, default=False)
    description_invoice: Mapped[str | None] = mapped_column(Text)  # larga: va a documentos
    description_store: Mapped[str | None] = mapped_column(Text)  # corta: links y tienda
    images: Mapped[list] = mapped_column(JSON, default=list)  # [{url, main}]
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id"))
    registration_number: Mapped[str | None] = mapped_column(String(80))
    cabys_code: Mapped[str | None] = mapped_column(String(13))
    cabys_description: Mapped[str | None] = mapped_column(String(300))
    tariff_code: Mapped[str | None] = mapped_column(String(20))  # partida arancelaria
    unit: Mapped[str] = mapped_column(String(10), default="Unid")
    min_stock: Mapped[int] = mapped_column(Integer, default=0)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    taxes: Mapped[list["ProductTax"]] = relationship(cascade="all, delete-orphan")


class ProductTax(Base):
    __tablename__ = "product_tax"
    __table_args__ = (UniqueConstraint("product_id", "tax_id", name="uq_product_tax"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))
    tax_id: Mapped[int] = mapped_column(ForeignKey("tax.id"))
    tax: Mapped[Tax] = relationship()
