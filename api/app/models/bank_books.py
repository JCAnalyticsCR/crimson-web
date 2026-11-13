"""Bancos: estados de cuenta importados y movimientos propios de la cuenta (reunion con la contadora, 10/11/2026).

- BankStatementImport: cada archivo del banco que se guardo (con el mapeo de columnas usado), para saber de donde
  salio cada linea y poder deshacer una importacion completa.
- BankMovement: lo que mueve la cuenta y no nace de un cobro, un gasto o un pago a proveedor: transferencias entre
  cuentas propias (deposito de caja al BAC, compra de dolares), intereses ganados, cargos bancarios sin factura y
  ajustes. Una transferencia son dos filas (sale de una cuenta, entra en la otra) con el mismo transfer_group.

No es contabilidad completa: es control de saldos y conciliacion contra el banco.
"""

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from ..core.db import Base
from .base import TenantMixin, utcnow


class BankStatementImport(TenantMixin, Base):
    __tablename__ = "bank_statement_import"

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("bank_account.id", name="fk_bank_statement_import_account"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    mapping: Mapped[dict | None] = mapped_column(JSON)  # columnas usadas (para repetir la misma lectura)
    date_from: Mapped[date | None] = mapped_column(Date)
    date_to: Mapped[date | None] = mapped_column(Date)
    rows: Mapped[int] = mapped_column(Integer, default=0)  # movimientos leidos del archivo
    added: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    closing_balance: Mapped[float | None] = mapped_column(Numeric(16, 2))  # saldo que reporta el banco en la ultima linea
    created_by: Mapped[int | None] = mapped_column(Integer)  # sin FK: la bitacora guarda quien fue
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BankMovement(TenantMixin, Base):
    __tablename__ = "bank_movement"

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(ForeignKey("bank_account.id", name="fk_bank_movement_account"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[float] = mapped_column(Numeric(16, 2))  # + entra a la cuenta, - sale
    # transferencia | deposito | interes | cargo | ajuste | otro
    kind: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(String(300))
    reference: Mapped[str | None] = mapped_column(String(120))
    transfer_group: Mapped[str | None] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(12), default="registrado")  # registrado | anulado
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
