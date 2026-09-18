"""Mi Tienda (plan 3.3), ordenes, cupones, recepcion de XML de compras y credenciales de API publica."""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin


class StorePage(TenantMixin, TimestampMixin, Base):
    """Pagina de la tienda: bloques JSON [{type, ...}] que renderiza el mismo componente en editor y sitio publico."""

    __tablename__ = "store_page"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_store_page_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80))  # "inicio", "nosotros"...
    title: Mapped[str] = mapped_column(String(160))
    blocks: Mapped[list] = mapped_column(JSON, default=list)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    in_nav: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class Coupon(TenantMixin, Base):
    __tablename__ = "coupon"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_coupon_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(8), default="percent")  # percent | amount
    value: Mapped[float] = mapped_column(Numeric(14, 5))
    min_total: Mapped[float] = mapped_column(Numeric(14, 5), default=0)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Order(TenantMixin, TimestampMixin, Base):
    """Pedido de tienda/POS/link previo a factura o tiquete (plan 3.9)."""

    __tablename__ = "order"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)
    channel: Mapped[str] = mapped_column(String(10), default="tienda")  # tienda | pos | link
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"))
    contact: Mapped[dict] = mapped_column(JSON, default=dict)  # nombre, correo, telefono, identificacion, direccion
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    subtotal: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    discount_total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    shipping: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    tax_total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    coupon_code: Mapped[str | None] = mapped_column(String(40))
    shipping_method: Mapped[str | None] = mapped_column(String(80))
    payment_method: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="nuevo", index=True)  # nuevo | pagado | preparando | enviado | entregado | cancelado
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoice.id"))
    notes: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list["OrderLine"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class OrderLine(Base):
    __tablename__ = "order_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3), default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(14, 5), default=0)
    discount_type: Mapped[str] = mapped_column(String(8), default="percent")
    discount_value: Mapped[float] = mapped_column(Numeric(14, 5), default=0)
    tax_rate: Mapped[float] = mapped_column(Numeric(6, 3), default=13)
    subtotal: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)


class ReceivedDocument(TenantMixin, TimestampMixin, Base):
    """Factura electronica recibida de un proveedor (XML): recepcion ante Hacienda + gasto (plan 3.4 / eje 3)."""

    __tablename__ = "received_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    clave: Mapped[str] = mapped_column(String(50), index=True)
    consecutive: Mapped[str | None] = mapped_column(String(20))
    issuer_name: Mapped[str | None] = mapped_column(String(200))
    issuer_id: Mapped[str | None] = mapped_column(String(30))
    issue_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    subtotal: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    tax_total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    iva_condition: Mapped[str] = mapped_column(String(12), default="credito")  # credito | no_credito | proporcional
    activity_code: Mapped[str | None] = mapped_column(String(10))
    action: Mapped[str | None] = mapped_column(String(12))  # aceptada | parcial | rechazada
    hacienda_status: Mapped[str] = mapped_column(String(20), default="pendiente")
    xml_document: Mapped[str | None] = mapped_column(Text)
    xml_response: Mapped[str | None] = mapped_column(Text)
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expense.id"))


class ApiCredential(TenantMixin, TimestampMixin, Base):
    """API publica: public key (kid) + secret (solo hash). Checkout por JWT HS256 firmado con el secreto; webhooks HMAC."""

    __tablename__ = "api_credential"

    id: Mapped[int] = mapped_column(primary_key=True)
    kid: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    secret_hash: Mapped[str] = mapped_column(String(64))
    secret_encrypted: Mapped[str] = mapped_column(Text)  # para firmar webhooks salientes
    webhook_url: Mapped[str | None] = mapped_column(String(400))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
