"""cuentas por pagar (reunion con la contadora 10/11): «Marcar pagada» con fecha real, referencia y pista del XML.

- expense.paid_date (DATE, indice): fecha en que salio el pago. Los gastos que ya estaban "pagado" toman la fecha de
  la factura (lo mas cercano que se sabe; la contadora corrige con «Desmarcar» + «Marcar pagada» si hace falta).
- expense.payment_reference: numero de referencia del banco (SINPE, transferencia, voucher, cheque).
- expense.xml_condition: condicion de venta del XML del proveedor (contado | credito | otro). Solo una pista. Se llena
  para los gastos con comprobante desde el corte con Fygaro (1/11/2026); si el XML dice credito y el gasto sigue
  pendiente con la condicion por defecto, pasa a credito con su vencimiento (fecha + PlazoCredito).
- supplier_payment.supplier_id admite NULL: un gasto sin proveedor tambien se marca pagado.

Bajar: se niega si hay pagos sin proveedor (perderian la columna obligatoria).

Revision ID: c5e8f1a2b9d4
Revises: b7c4e1a9d302
Create Date: 2026-11-12 10:00:00
"""

import re
from collections.abc import Sequence
from datetime import date, timedelta

import sqlalchemy as sa

from alembic import op

revision: str = "c5e8f1a2b9d4"
down_revision: str | None = "b7c4e1a9d302"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CORTE = "2026-11-01"
_COND = re.compile(r"<(?:\w+:)?CondicionVenta>\s*(\d+)\s*</", re.S)
_PLAZO = re.compile(r"<(?:\w+:)?PlazoCredito>\s*(\d+)\s*</", re.S)
CREDITO = {"02", "08", "10"}  # credito, servicios al Estado a credito, venta a credito con IVA hasta 90 dias (4.4)


def _condicion(xml: str | None) -> tuple[str | None, int]:
    if not xml:
        return None, 0
    m = _COND.search(xml)
    if not m:
        return None, 0
    code = m.group(1).zfill(2)
    plazo = _PLAZO.search(xml)
    dias = int(plazo.group(1)) if plazo else 0
    return ("credito" if code in CREDITO else "contado" if code == "01" else "otro"), min(dias, 3650)


def upgrade() -> None:
    with op.batch_alter_table("expense", schema=None) as b:
        b.add_column(sa.Column("paid_date", sa.Date(), nullable=True))
        b.add_column(sa.Column("payment_reference", sa.String(length=120), nullable=True))
        b.add_column(sa.Column("xml_condition", sa.String(length=10), nullable=True))
        b.create_index("ix_expense_paid_date", ["paid_date"], unique=False)
    with op.batch_alter_table("supplier_payment", schema=None) as b:
        b.alter_column("supplier_id", existing_type=sa.Integer(), nullable=True)

    bind = op.get_bind()
    bind.execute(sa.text("UPDATE expense SET paid_date = date WHERE status = 'pagado' AND paid_date IS NULL"))
    rows = bind.execute(
        sa.text(
            "SELECT e.id, e.date, e.status, e.purchase_type, e.due_date, r.xml_document FROM expense e"
            " JOIN received_document r ON r.id = e.received_document_id WHERE e.date >= :corte"
        ),
        {"corte": CORTE},
    )
    for eid, fecha, status, purchase_type, due, xml in rows.fetchall():
        cond, dias = _condicion(xml)
        if cond is None:
            continue
        cambios = {"cond": cond, "id": eid}
        sql = "UPDATE expense SET xml_condition = :cond"
        if cond == "credito" and status == "registrado" and purchase_type == "contado" and due is None:
            if isinstance(fecha, str):
                fecha = date.fromisoformat(fecha[:10])
            sql += ", purchase_type = 'credito', due_date = :due"
            cambios["due"] = fecha + timedelta(days=dias)
        bind.execute(sa.text(sql + " WHERE id = :id"), cambios)


def downgrade() -> None:
    bind = op.get_bind()
    sin_proveedor = bind.execute(sa.text("SELECT COUNT(*) FROM supplier_payment WHERE supplier_id IS NULL")).scalar_one()
    if sin_proveedor:
        raise RuntimeError(f"{sin_proveedor} pagos a proveedor no tienen proveedor: asígnelo o anúlelos antes de bajar esta migración")
    with op.batch_alter_table("supplier_payment", schema=None) as b:
        b.alter_column("supplier_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("expense", schema=None) as b:
        b.drop_index("ix_expense_paid_date")
        b.drop_column("xml_condition")
        b.drop_column("payment_reference")
        b.drop_column("paid_date")
