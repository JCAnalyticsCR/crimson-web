"""margenes: el precio del proyecto pasa a ser la venta sin IVA (subtotal - descuento de su cotizacion)

Andres: "Todos los calculos de margen usaran la venta sin IVA". Antes project.price guardaba el total con IVA y
el margen salia inflado. Solo se recalculan los proyectos con cotizacion; los demas quedan como estan.

Revision ID: d0cb4e92f968
Revises: c41b7e9d2a60
Create Date: 2026-10-12 10:00:00
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "d0cb4e92f968"
down_revision: str | None = "c41b7e9d2a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _q2(x) -> Decimal:
    return Decimal(str(x or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _recalcular(con_iva: bool) -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT p.id, q.subtotal, q.discount_total, q.total FROM project p JOIN quote q ON q.id = p.quote_id WHERE p.quote_id IS NOT NULL")
    ).fetchall()
    # tipado: SQLite no acepta Decimal crudo como parametro
    update = sa.text("UPDATE project SET price = :precio WHERE id = :pid").bindparams(sa.bindparam("precio", type_=sa.Numeric(16, 2)))
    for pid, subtotal, descuento, total in rows:
        precio = _q2(total) if con_iva else _q2(Decimal(str(subtotal or 0)) - Decimal(str(descuento or 0)))
        conn.execute(update, {"precio": precio, "pid": pid})


def upgrade() -> None:
    _recalcular(con_iva=False)


def downgrade() -> None:
    # vuelve al comportamiento anterior: el total con IVA de la cotizacion
    _recalcular(con_iva=True)
