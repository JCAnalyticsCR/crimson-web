"""Asientos de diario para exportar a QuickBooks Online (contabilidad externa).

Crimson NO lleva partida doble internamente: genera los asientos a partir de los documentos
existentes (facturas, NC, cobros, gastos, planillas, comisiones, costo de ventas) y los
deja listos para que la contadora los importe manualmente en QBO.

Un asiento por documento, número estable JE-<tipo>-<id>, idempotente: regenerar no duplica.
"""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.db import Base
from .base import TenantMixin, TimestampMixin, utcnow

# ---------------------------------------------------------------------------
# Catálogo de roles contables (fijo — no lo edita el usuario)
# ---------------------------------------------------------------------------

ACCOUNTING_ROLES: list[tuple[str, str]] = [
    # Ingresos
    ("ventas_equipos",         "Ventas de equipos y productos"),
    ("ventas_servicios",       "Ventas de servicios"),
    ("ventas_proyectos",       "Ventas de proyectos"),
    ("descuentos",             "Descuentos en ventas"),
    ("otros_ingresos",         "Otros ingresos"),
    # IVA por pagar (por tarifa)
    ("iva_por_pagar_13",       "IVA por pagar 13 %"),
    ("iva_por_pagar_4",        "IVA por pagar 4 %"),
    ("iva_por_pagar_2",        "IVA por pagar 2 %"),
    ("iva_por_pagar_1",        "IVA por pagar 1 %"),
    ("iva_por_pagar_0",        "IVA 0 % / exento"),
    # IVA de compras
    ("iva_acreditable",        "IVA acreditable de compras"),
    ("iva_no_acreditable",     "IVA no acreditable (gasto)"),
    # Cuentas por cobrar / pagar
    ("cxc_clientes_crc",       "Cuentas por cobrar – clientes CRC"),
    ("cxc_clientes_usd",       "Cuentas por cobrar – clientes USD"),
    ("cxp_proveedores_crc",    "Cuentas por pagar – proveedores CRC"),
    ("cxp_proveedores_usd",    "Cuentas por pagar – proveedores USD"),
    # Efectivo y medios de cobro (se añaden banco_<id> dinámicamente)
    ("caja_efectivo",          "Caja / efectivo"),
    ("sinpe_por_cobrar",       "SINPE / transferencia por acreditar"),
    ("tarjeta_por_cobrar",     "Tarjeta por cobrar (batch pendiente)"),
    ("onvo_por_cobrar",        "ONVO / pasarela por cobrar"),
    # Inventario y costo
    ("inventario",             "Inventario de mercancía"),
    ("costo_ventas",           "Costo de ventas"),
    # Planilla
    ("sueldos",                "Gasto de sueldos y salarios"),
    ("cargas_ccss_patronal",   "Cargas sociales patronales (CCSS)"),
    ("ret_ccss_obrera",        "CCSS obrera retenida por pagar"),
    ("ret_renta",              "Impuesto al salario retenido por pagar"),
    ("ret_otras",              "Otras deducciones de planilla por pagar"),
    ("provision_aguinaldo",    "Provisión aguinaldo"),
    ("provision_vacaciones",   "Provisión vacaciones"),
    # Comisiones
    ("comisiones_gasto",       "Gasto de comisiones"),
    ("comisiones_por_pagar",   "Comisiones por pagar"),
    # Diferencial cambiario y redondeo
    ("diferencial_ganancia",   "Diferencial cambiario – ganancia"),
    ("diferencial_perdida",    "Diferencial cambiario – pérdida"),
    ("redondeo",               "Diferencias de redondeo"),
]

# Roles cuya clave se genera dinámicamente (no están en ACCOUNTING_ROLES):
#   banco_<bank_account_id>       → "Cuenta bancaria: <name>"
#   gasto_cat_<category_id>       → "Gasto: <category_name>"


def bank_role(bank_account_id: int) -> str:
    return f"banco_{bank_account_id}"


def expense_cat_role(category_id: int) -> str:
    return f"gasto_cat_{category_id}"


# ---------------------------------------------------------------------------
# Plan de cuentas importado de QBO
# ---------------------------------------------------------------------------

class QboAccount(TenantMixin, Base):
    """Una línea del plan de cuentas exportado de QuickBooks Online.

    Reimportable sin perder el mapeo: si el full_name ya existe, se actualiza;
    si no, se inserta. Las cuentas que desaparecen en la reimportación se marcan
    activas=False (no se borran porque pueden tener asientos exportados).
    """

    __tablename__ = "qbo_account"
    __table_args__ = (
        UniqueConstraint("tenant_id", "full_name", name="uq_qbo_account_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # El nombre completo tal como QBO lo espera en la columna Account del CSV de importación.
    # Para cuentas con jerarquía: "Padre:Hijo" (el separador que usa QBO).
    full_name: Mapped[str] = mapped_column(String(300))
    number: Mapped[str | None] = mapped_column(String(40))
    account_type: Mapped[str | None] = mapped_column(String(80))    # Bank, Accounts Receivable, ...
    detail_type: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(300))
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # UUID del último lote de importación (para marcar inactivas las que no aparecieron)
    import_batch: Mapped[str | None] = mapped_column(String(40))


# ---------------------------------------------------------------------------
# Mapeo de roles a cuentas QBO
# ---------------------------------------------------------------------------

class AccountingRoleMapping(TenantMixin, Base):
    """Un rol contable (clave fija o dinámica) apuntando a una QboAccount."""

    __tablename__ = "accounting_role_mapping"
    __table_args__ = (UniqueConstraint("tenant_id", "role_key", name="uq_arm_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    role_key: Mapped[str] = mapped_column(String(80))
    qbo_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("qbo_account.id", name="fk_arm_qbo", ondelete="SET NULL")
    )
    updated_by: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped["QboAccount | None"] = relationship(lazy="joined")


# ---------------------------------------------------------------------------
# Control de periodos (cierre de mes)
# ---------------------------------------------------------------------------

class AccountingPeriod(TenantMixin, Base):
    """Un mes contable y su estado.

    abierto   → se pueden agregar y regenerar asientos.
    exportado → mes cerrado; documentos nuevos o cambiados generan un AJUSTE
                en el primer mes abierto.
    """

    __tablename__ = "accounting_period"
    __table_args__ = (UniqueConstraint("tenant_id", "month", name="uq_acc_period_month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7), index=True)  # "2026-10"
    # abierto | exportado
    status: Mapped[str] = mapped_column(String(12), default="abierto")
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exported_by: Mapped[int | None] = mapped_column(Integer)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_by: Mapped[int | None] = mapped_column(Integer)
    reopen_reason: Mapped[str | None] = mapped_column(String(300))


# ---------------------------------------------------------------------------
# Registro de exportaciones
# ---------------------------------------------------------------------------

class AccountingExport(TenantMixin, TimestampMixin, Base):
    """Bitácora de cada exportación de un mes."""

    __tablename__ = "accounting_export"

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    period_id: Mapped[int] = mapped_column(
        ForeignKey("accounting_period.id", name="fk_aexport_period")
    )
    exported_by: Mapped[int] = mapped_column(Integer)
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    # reexportación admin: el mismo mes se puede volver a exportar con motivo explícito
    is_reexport: Mapped[bool] = mapped_column(Boolean, default=False)
    reexport_reason: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Asientos de diario
# ---------------------------------------------------------------------------

SOURCE_TYPES = (
    "invoice",           # Factura / TE / FEE
    "credit_note",       # Nota de crédito
    "payment",           # Cobro al cliente
    "expense",           # Gasto / factura de proveedor
    "supplier_payment",  # Pago a proveedor (gastos crédito → pagados; futuro: SupplierPayment)
    "payroll",           # Planilla (PayrollRun)
    "commission",        # Comisión pagada (Commission)
    "cogs",              # Costo de ventas de una factura (StockMovements)
)


class JournalEntry(TenantMixin, Base):
    """Un asiento de diario por documento fuente.

    Número estable: JE-INV-<id>, JE-PMT-<id>, JE-EXP-<id>, etc.
    Idempotente: upsert_entry borra las líneas viejas y escribe las nuevas.
    Si el documento cae en un mes cerrado, kind='ajuste' y la fecha es la del
    primer día del primer mes abierto; adjusts_month guarda el mes original.
    """

    __tablename__ = "journal_entry"
    __table_args__ = (
        # kind en la clave: permite un asiento normal + uno de ajuste por mismo documento
        UniqueConstraint("tenant_id", "source_type", "source_id", "kind", name="uq_jentry_source"),
        Index("ix_jentry_month", "tenant_id", "month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), index=True)   # JE-INV-123
    source_type: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[int] = mapped_column(Integer)
    date: Mapped[date] = mapped_column(Date, index=True)          # fecha contable = fecha del documento
    month: Mapped[str] = mapped_column(String(7))                 # "YYYY-MM"
    description: Mapped[str] = mapped_column(String(300))
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    # normal | ajuste
    kind: Mapped[str] = mapped_column(String(10), default="normal")
    # mes original del documento cuando es un ajuste (el mes cerrado que no se pudo tocar)
    adjusts_month: Mapped[str | None] = mapped_column(String(7))
    period_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounting_period.id", name="fk_jentry_period")
    )
    export_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounting_export.id", name="fk_jentry_export")
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lines: Mapped[list["JournalLine"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="JournalLine.position"
    )


class JournalLine(Base):
    """Una línea de debe o haber dentro de un asiento."""

    __tablename__ = "journal_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="CASCADE", name="fk_jline_entry"), index=True
    )
    # Rol contable (se resuelve al exportar con el mapeo vigente; no se fija aquí para que
    # un cambio en el mapeo antes de exportar se refleje sin regenerar el asiento).
    role_key: Mapped[str] = mapped_column(String(80))
    side: Mapped[str] = mapped_column(String(5))    # debe | haber
    amount: Mapped[float] = mapped_column(Numeric(16, 2))  # siempre positivo, 2 dec
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    # tipo de cambio si currency != CRC (para líneas en USD o la cuenta bancaria es USD)
    fx: Mapped[float | None] = mapped_column(Numeric(12, 5))
    description: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str | None] = mapped_column(String(160))   # cliente o proveedor (Name en QBO)
    position: Mapped[int] = mapped_column(Integer, default=0)
