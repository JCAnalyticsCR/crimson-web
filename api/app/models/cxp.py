"""Cuentas por pagar: pagos a proveedor y sus aplicaciones contra gastos de crédito."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin


class SupplierPayment(TenantMixin, TimestampMixin, Base):
    """Pago realizado a un proveedor: puede cubrir uno o varios gastos de crédito (pagos parciales permitidos)."""

    __tablename__ = "supplier_payment"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("supplier.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[float] = mapped_column(Numeric(16, 5))
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    # tipo de cambio del servicio único services/fx.py en la fecha del pago
    fx_rate: Mapped[float] = mapped_column(Numeric(12, 5), default=1)
    # transferencia | sinpe | cheque | tarjeta | efectivo
    method: Mapped[str] = mapped_column(String(20))
    bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_account.id"))
    reference: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    # registrado | anulado
    status: Mapped[str] = mapped_column(String(12), default="registrado")
    void_reason: Mapped[str | None] = mapped_column(String(300))
    voided_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_supplier_payment_voided_by"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_supplier_payment_created_by"))

    applications: Mapped[list["SupplierPaymentApplication"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class SupplierPaymentApplication(Base):
    """Aplicación de un pago a un gasto de crédito concreto (monto en la moneda del gasto)."""

    __tablename__ = "supplier_payment_application"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_payment.id", ondelete="CASCADE", name="fk_spa_payment"), index=True
    )
    expense_id: Mapped[int] = mapped_column(
        ForeignKey("expense.id", name="fk_spa_expense"), index=True
    )
    # monto aplicado en la moneda del GASTO (no del pago)
    amount: Mapped[float] = mapped_column(Numeric(16, 5))
