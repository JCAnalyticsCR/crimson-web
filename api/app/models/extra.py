"""Sesion 4: multimedia, CRM ampliado, variantes, planillas, conciliacion bancaria, eventos con QR y acceso de soporte."""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin


# ---------- Multimedia: imagenes de productos, logo de tienda, adjuntos de gastos ----------
class Media(TenantMixin, TimestampMixin, Base):
    """Archivo guardado en Postgres (bytea). La URL publica usa una llave aleatoria, nunca el id secuencial."""

    __tablename__ = "media"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))


# ---------- CRM: contactos y notas del cliente (la linea de tiempo se arma con documentos + notas) ----------
class CustomerContact(TenantMixin, Base):
    __tablename__ = "customer_contact"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    receives_invoices: Mapped[bool] = mapped_column(Boolean, default=False)


class CustomerNote(TenantMixin, TimestampMixin, Base):
    __tablename__ = "customer_note"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))


# ---------- Variantes de producto (color, tamano...): comparten existencias con el producto ----------
class ProductVariant(TenantMixin, Base):
    __tablename__ = "product_variant"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_variant_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))  # "Blanco / 4MP"
    code: Mapped[str] = mapped_column(String(60))
    price: Mapped[float | None] = mapped_column(Numeric(14, 5))  # None = precio del producto
    options: Mapped[dict] = mapped_column(JSON, default=dict)  # {"Color": "Blanco"}
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


# ---------- Planillas (CR): CCSS obrero/patronal, impuesto al salario por tramos, provisiones ----------
class Employee(TenantMixin, TimestampMixin, Base):
    __tablename__ = "employee"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    id_number: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    position: Mapped[str | None] = mapped_column(String(120))
    salary: Mapped[float] = mapped_column(Numeric(14, 2))  # salario bruto mensual
    frequency: Mapped[str] = mapped_column(String(10), default="mensual")  # mensual | quincenal
    start_date: Mapped[date | None] = mapped_column(Date)
    iban: Mapped[str | None] = mapped_column(String(40))
    children: Mapped[int] = mapped_column(Integer, default=0)  # creditos fiscales por hijo
    spouse_credit: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PayrollRun(TenantMixin, TimestampMixin, Base):
    __tablename__ = "payroll_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    frequency: Mapped[str] = mapped_column(String(10), default="mensual")
    status: Mapped[str] = mapped_column(String(12), default="borrador")  # borrador | aprobada | pagada
    gross: Mapped[float] = mapped_column(Numeric(16, 2), default=0)
    ccss_worker: Mapped[float] = mapped_column(Numeric(16, 2), default=0)
    income_tax: Mapped[float] = mapped_column(Numeric(16, 2), default=0)
    other_deductions: Mapped[float] = mapped_column(Numeric(16, 2), default=0)
    net: Mapped[float] = mapped_column(Numeric(16, 2), default=0)
    ccss_employer: Mapped[float] = mapped_column(Numeric(16, 2), default=0)
    provisions: Mapped[float] = mapped_column(Numeric(16, 2), default=0)  # aguinaldo + vacaciones
    rates: Mapped[dict] = mapped_column(JSON, default=dict)  # tasas usadas (auditoria)
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expense.id"))
    notes: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list["PayrollLine"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class PayrollLine(Base):
    __tablename__ = "payroll_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("payroll_run.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id"))
    employee_name: Mapped[str] = mapped_column(String(160))
    base: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    overtime: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    bonus: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    other_deductions: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    gross: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    ccss_worker: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    income_tax: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    net: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    ccss_employer: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    provisions: Mapped[float] = mapped_column(Numeric(14, 2), default=0)


# ---------- Conciliacion bancaria: lineas del estado de cuenta contra pagos y gastos ----------
class BankStatementLine(TenantMixin, Base):
    __tablename__ = "bank_statement_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("bank_account.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str] = mapped_column(String(300))
    reference: Mapped[str | None] = mapped_column(String(120))
    amount: Mapped[float] = mapped_column(Numeric(16, 2))  # + credito (entra), - debito (sale)
    batch: Mapped[str] = mapped_column(String(40), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)  # evita importar dos veces la misma linea
    status: Mapped[str] = mapped_column(String(12), default="pendiente")  # pendiente | conciliado | ignorado
    matched_type: Mapped[str | None] = mapped_column(String(10))  # payment | expense
    matched_id: Mapped[int | None] = mapped_column(Integer)
    matched_by: Mapped[str | None] = mapped_column(String(10))  # auto | manual
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ---------- Eventos y entradas con QR ----------
class Event(TenantMixin, TimestampMixin, Base):
    __tablename__ = "event"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_event_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    venue: Mapped[str | None] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    image_url: Mapped[str | None] = mapped_column(String(400))
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    tax_rate: Mapped[float] = mapped_column(Numeric(6, 3), default=13)
    status: Mapped[str] = mapped_column(String(12), default="borrador")  # borrador | publicado | cerrado

    ticket_types: Mapped[list["TicketType"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="TicketType.id")


class TicketType(Base):
    __tablename__ = "ticket_type"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    price: Mapped[float] = mapped_column(Numeric(14, 2), default=0)  # precio final (IVA incluido)
    quantity: Mapped[int] = mapped_column(Integer, default=100)
    max_per_order: Mapped[int] = mapped_column(Integer, default=10)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Ticket(TenantMixin, TimestampMixin, Base):
    __tablename__ = "ticket"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"), index=True)
    ticket_type_id: Mapped[int] = mapped_column(ForeignKey("ticket_type.id"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("order.id"), index=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    holder_name: Mapped[str] = mapped_column(String(160))
    holder_email: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), default="pendiente")  # pendiente | valido | usado | anulado
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_in_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))


# ---------- Acceso de soporte auditado: el admin lo concede por horas y todo queda en bitacora ----------
class SupportGrant(TenantMixin, TimestampMixin, Base):
    __tablename__ = "support_grant"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(String(300))
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
