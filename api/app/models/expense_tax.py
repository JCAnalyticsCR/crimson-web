"""Desglose del IVA de un gasto por tarifa y reglas proveedor -> categoria (reunion con la contadora, 10/11/2026).

expense_tax_line: una fila por (tipo, tarifa) de un gasto. Las facturas de proveedor mezclan tarifas (13 %, 4 %, 2 %,
1 %, exento, exonerado...) y cada parte se declara y se contabiliza con la suya. Sale del XML de Hacienda (LineaDetalle)
o, en un gasto a mano, es una sola parte con la tarifa del gasto. Los totales de expense siguen siendo la suma de sus
partes; services/expense_tax.py lo garantiza y vuelve a calcular si alguien cambio el gasto despues.

supplier_category_rule: «este proveedor siempre es medicamentos». La categoria que se le pone sola a un gasto nuevo de
ese proveedor; aprende de la ultima correccion de una persona (services/expense_category.py).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..core.db import Base
from .base import TenantMixin, utcnow


class ExpenseTaxLine(TenantMixin, Base):
    __tablename__ = "expense_tax_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    expense_id: Mapped[int] = mapped_column(ForeignKey("expense.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    # gravado | exento | exonerado | otro_impuesto | otros_cargos
    kind: Mapped[str] = mapped_column(String(16), default="gravado")
    rate: Mapped[float] = mapped_column(Numeric(6, 3), default=0)
    rate_code: Mapped[str | None] = mapped_column(String(2))  # CodigoTarifaIVA del XML (Nota 8.1); None en gastos a mano
    base: Mapped[float] = mapped_column(Numeric(16, 5), default=0)
    tax: Mapped[float] = mapped_column(Numeric(16, 5), default=0)  # impuesto neto (ya sin la exoneracion)
    exonerated: Mapped[float] = mapped_column(Numeric(16, 5), default=0)  # MontoExoneracion
    credit: Mapped[float] = mapped_column(Numeric(16, 5), default=0)  # IVA acreditable de esta parte
    prorrata: Mapped[bool] = mapped_column(Boolean, default=False)  # condicion 05: el credito sale de la prorrata del mes
    currency: Mapped[str] = mapped_column(String(3), default="CRC")
    source: Mapped[str] = mapped_column(String(8), default="manual")  # xml | manual
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SupplierCategoryRule(TenantMixin, Base):
    __tablename__ = "supplier_category_rule"
    __table_args__ = (UniqueConstraint("tenant_id", "supplier_id", name="uq_supplier_category_rule"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("supplier.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("expense_category.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(10), default="aprendida")  # aprendida | manual
    hits: Mapped[int] = mapped_column(Integer, default=0)  # gastos categorizados solos con esta regla
    updated_by: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


@event.listens_for(Session, "before_flush")
def _categoria_automatica(session, ctx, instances):  # noqa: ANN001
    """Categoria automatica por proveedor y aprendizaje al corregir (services/expense_category). Importa tarde: los
    modelos no dependen de los servicios al cargarse."""
    if not any(type(o).__name__ == "Expense" for o in (*session.new, *session.dirty)):
        return
    from ..services.expense_category import before_flush

    before_flush(session, ctx, instances)
