from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin


class BankAccount(TenantMixin, Base):
    __tablename__ = "bank_account"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    bank: Mapped[str | None] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    number: Mapped[str | None] = mapped_column(String(60))  # IBAN / referencia
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Payment(TenantMixin, TimestampMixin, Base):
    """Separa metodo (efectivo, tarjeta, sinpe, transferencia, onvo, paypal) de tipo de transaccion.

    kind: autorizacion | captura | devolucion | reembolso | reautorizacion | void
    Devolucion y reembolso restan del saldo cobrado (monto negativo en el calculo).
    """

    __tablename__ = "payment"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id", ondelete="RESTRICT"), index=True)
    method: Mapped[str] = mapped_column(String(20), default="efectivo")
    kind: Mapped[str] = mapped_column(String(20), default="captura")
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    amount: Mapped[float] = mapped_column(Numeric(16, 5))
    tip: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_account.id"))
    external_ref: Mapped[str | None] = mapped_column(String(120))  # comprobante SINPE, id ONVO...
    provider: Mapped[str] = mapped_column(String(20), default="manual")  # manual | onvo | paypal
    provider_event: Mapped[dict | None] = mapped_column(JSON)  # evento crudo del webhook
    paid_at: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="confirmado")  # confirmado | pendiente | fallido
    notify_customer: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))

    invoice: Mapped["Invoice"] = relationship(back_populates="payments")  # noqa: F821


class PaymentLink(TenantMixin, TimestampMixin, Base):
    """URL publica unica por factura con token firmado y expiracion; registra abierto/pagado."""

    __tablename__ = "payment_link"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(600), unique=True)
    url: Mapped[str] = mapped_column(String(700))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_count: Mapped[int] = mapped_column(default=0)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
