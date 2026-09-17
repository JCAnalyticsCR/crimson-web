from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin


class BillingGroup(TenantMixin, Base):
    """Grupo de facturacion: prefijo + consecutivo por tipo de documento.

    Consecutivo v4.4 = sucursal(3) + terminal(5) + tipo(2) + secuencia(10). Nunca se salta ni reutiliza.
    doc_type: FE (factura electronica) | TE (tiquete) | FEE (exportacion) | NC | ND | COT (cotizacion).
    """

    __tablename__ = "billing_group"
    __table_args__ = (UniqueConstraint("tenant_id", "doc_type", "prefix", name="uq_billing_group"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(4))
    prefix: Mapped[str] = mapped_column(String(10))
    branch: Mapped[str] = mapped_column(String(3), default="001")
    terminal: Mapped[str] = mapped_column(String(5), default="00001")
    current: Mapped[int] = mapped_column(Integer, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    ecommerce: Mapped[bool] = mapped_column(Boolean, default=False)


class _DocBase(TenantMixin, TimestampMixin):
    """Campos comunes de cotizacion y factura (columna derecha del editor)."""

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)  # ej. COT-000188 / FEC-000512
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id", ondelete="RESTRICT"))
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    fx_sell: Mapped[float] = mapped_column(Numeric(12, 5), default=1)  # tipo de cambio guardado en el documento
    fx_buy: Mapped[float] = mapped_column(Numeric(12, 5), default=1)
    issue_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    discount_type: Mapped[str] = mapped_column(String(8), default="percent")  # percent | amount
    discount_value: Mapped[float] = mapped_column(Numeric(14, 5), default=0)
    subtotal: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    discount_total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    tax_total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    internal_notes: Mapped[str | None] = mapped_column(Text)  # nunca se imprimen
    external_notes: Mapped[str | None] = mapped_column(Text)  # van al PDF y al correo
    external_order: Mapped[str | None] = mapped_column(String(60))
    activity_code: Mapped[str | None] = mapped_column(String(10))  # ej. 6202.0
    medical_exemption_card: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="creado", index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))


class _LineBase:
    id: Mapped[int] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"))
    code: Mapped[str | None] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    cabys_code: Mapped[str | None] = mapped_column(String(13))
    unit: Mapped[str] = mapped_column(String(10), default="Unid")
    quantity: Mapped[float] = mapped_column(Numeric(14, 3), default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(14, 5), default=0)
    discount_type: Mapped[str] = mapped_column(String(8), default="percent")
    discount_value: Mapped[float] = mapped_column(Numeric(14, 5), default=0)
    tax_rate: Mapped[float] = mapped_column(Numeric(6, 3), default=13)
    tax_code: Mapped[str] = mapped_column(String(4), default="01")
    tax_rate_code: Mapped[str] = mapped_column(String(4), default="08")
    subtotal: Mapped[float] = mapped_column(Numeric(16, 5), default=0)  # cantidad x precio - descuento
    tax_amount: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)


class Quote(_DocBase, Base):
    __tablename__ = "quote"
    # status: creado | enviada | convertida | anulada | vencida

    converted_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoice.id"))
    lines: Mapped[list["QuoteLine"]] = relationship(cascade="all, delete-orphan", order_by="QuoteLine.position", lazy="selectin")


class QuoteLine(_LineBase, Base):
    __tablename__ = "quote_line"
    quote_id: Mapped[int] = mapped_column(ForeignKey("quote.id", ondelete="CASCADE"), index=True)


class Invoice(_DocBase, Base):
    __tablename__ = "invoice"
    # status: creado | enviada | pagada | parcial | vencida | anulada

    doc_type: Mapped[str] = mapped_column(String(4), default="FE")
    consecutive: Mapped[str | None] = mapped_column(String(20))  # 20 digitos v4.4 (al emitir)
    clave: Mapped[str | None] = mapped_column(String(50))  # clave Hacienda (al emitir)
    sale_condition: Mapped[str] = mapped_column(String(4), default="01")  # 01 contado, 02 credito
    credit_days: Mapped[int] = mapped_column(Integer, default=0)
    payment_method: Mapped[str] = mapped_column(String(4), default="01")  # 01 efectivo, 02 tarjeta, 04 transf, 06 SINPE
    balance: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quote.id"))
    einvoice_status: Mapped[str] = mapped_column(String(20), default="sin_emitir")  # sin_emitir | pendiente | aceptada | rechazada

    lines: Mapped[list["InvoiceLine"]] = relationship(cascade="all, delete-orphan", order_by="InvoiceLine.position", lazy="selectin")
    payments: Mapped[list["Payment"]] = relationship(back_populates="invoice", lazy="selectin")  # noqa: F821


class InvoiceLine(_LineBase, Base):
    __tablename__ = "invoice_line"
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id", ondelete="CASCADE"), index=True)
