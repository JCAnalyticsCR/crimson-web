"""reportes: grupo de cada categoria de gasto en el estado de resultados (reunion 2, pedido 1)

`expense_category.pnl_group`: "costo_ventas" (resta antes de la utilidad bruta) u "operativo" (gastos de operacion).
NULL = se decide por el nombre de la categoria (services/profit_loss.group_of): compras, equipos, materiales,
mercaderia, inventario o subcontratos van a costo de ventas; lo demas, a operacion. Sin relleno: las categorias de
antes siguen por su nombre hasta que contabilidad elija.

downgrade quita la columna (los gastos no cambian).

Revision ID: a7c4e2f91b30
Revises: f3a8d2c6b917
Create Date: 2026-10-22 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c4e2f91b30"
down_revision: str | None = "f3a8d2c6b917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("expense_category") as b:
        b.add_column(sa.Column("pnl_group", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("expense_category") as b:
        b.drop_column("pnl_group")
