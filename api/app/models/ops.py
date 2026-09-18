"""Inventario, contabilidad, recurrencias, factura electronica, correo saliente y webhooks."""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin


# ---------- Inventario (plan 5.4): el saldo se calcula desde el ledger, nunca se edita ----------
class Warehouse(TenantMixin, Base):
    __tablename__ = "warehouse"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(160))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class StockMovement(TenantMixin, Base):
    """kind: entrada | salida | ajuste | transferencia_out | transferencia_in | venta | devolucion."""

    __tablename__ = "stock_movement"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouse.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3))  # positivo entra, negativo sale
    unit_cost: Mapped[float | None] = mapped_column(Numeric(14, 5))
    reference: Mapped[str | None] = mapped_column(String(120))  # FEC-000012, OC-4, ajuste
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoice.id"))
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))


# ---------- Contabilidad (plan 3.7) ----------
class ExpenseCategory(TenantMixin, Base):
    __tablename__ = "expense_category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Expense(TenantMixin, TimestampMixin, Base):
    __tablename__ = "expense"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("expense_category.id"))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id"))
    description: Mapped[str] = mapped_column(String(300))
    date: Mapped[date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    subtotal: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    tax_rate: Mapped[float] = mapped_column(Numeric(6, 3), default=13)
    tax_amount: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    total: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    iva_credit: Mapped[str] = mapped_column(String(12), default="credito")  # credito | no_credito | proporcional
    bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_account.id"))
    reference: Mapped[str | None] = mapped_column(String(120))
    received_clave: Mapped[str | None] = mapped_column(String(50))  # clave del XML recibido (Fase 2)
    attachment_url: Mapped[str | None] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(20), default="registrado")  # registrado | pagado | anulado
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))


# ---------- Recurrencias (plan 4): plantilla -> factura en la fecha programada ----------
class Recurrence(TenantMixin, TimestampMixin, Base):
    __tablename__ = "recurrence"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(10), default="factura", server_default="factura")  # factura | gasto
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"))  # solo facturas
    template: Mapped[dict] = mapped_column(JSON)  # DocumentIn (factura) o ExpenseIn (gasto) serializado
    frequency: Mapped[str] = mapped_column(String(12), default="mensual")  # semanal | quincenal | mensual | anual
    next_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date | None] = mapped_column(Date)
    auto_send: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_charge: Mapped[bool] = mapped_column(Boolean, default=False)  # con tarjeta tokenizada (Fase 3)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoice.id"))
    last_expense_id: Mapped[int | None] = mapped_column(ForeignKey("expense.id"))
    runs: Mapped[int] = mapped_column(Integer, default=0)


# ---------- Factura electronica (plan 5.1) ----------
class EInvoiceDocument(TenantMixin, TimestampMixin, Base):
    """Un registro por comprobante enviado a Hacienda (FE/TE/FEE/NC/ND). XML y respuesta se guardan integros (5 anos)."""

    __tablename__ = "einvoice_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(4))
    consecutive: Mapped[str] = mapped_column(String(20))
    clave: Mapped[str | None] = mapped_column(String(50), index=True)
    reference_clave: Mapped[str | None] = mapped_column(String(50))  # NC/ND: clave del documento original
    provider: Mapped[str] = mapped_column(String(20))
    provider_ref: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="pendiente")  # pendiente | enviado | aceptada | rechazada | error
    hacienda_message: Mapped[str | None] = mapped_column(Text)
    xml_document: Mapped[str | None] = mapped_column(Text)
    xml_response: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------- Correo saliente (outbox) y webhooks (plan 6, 5.3) ----------
class EmailOutbox(TenantMixin, TimestampMixin, Base):
    __tablename__ = "email_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    to: Mapped[str] = mapped_column(String(300))
    bcc: Mapped[str | None] = mapped_column(String(600))
    subject: Mapped[str] = mapped_column(String(300))
    html: Mapped[str] = mapped_column(Text)
    attachments: Mapped[list] = mapped_column(JSON, default=list)  # [{name, kind, ref}]
    entity: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pendiente")  # pendiente | enviado | error | simulado
    provider_id: Mapped[str | None] = mapped_column(String(120))
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookEvent(Base):
    """Evento crudo de proveedor (ONVO, PayPal, fiscal). Idempotente por (provider, external_id)."""

    __tablename__ = "webhook_event"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_webhook_provider_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)
    external_id: Mapped[str] = mapped_column(String(160))
    event_type: Mapped[str] = mapped_column(String(80))
    signature_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(Text)


class PaymentGatewayConfig(TenantMixin, Base):
    """Pasarelas por empresa (plan 3.8). El secreto se guarda cifrado con la clave del servidor."""

    __tablename__ = "payment_gateway_config"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", name="uq_gateway_tenant_provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))  # onvo | paypal
    client_id: Mapped[str | None] = mapped_column(String(200))
    secret_encrypted: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(10), default="test")  # test | live

    tenant = relationship("Tenant")
