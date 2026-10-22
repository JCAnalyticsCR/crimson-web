"""pedidos: "Vendedor que atendio" del pedido web (reunion 2, item 12)

Por defecto el vendedor NO comisiona por atender pedidos web (es parte de su trabajo). Solo comisiona el vendedor del
cliente: el que el admin pone como "Vendedor que atendio" (o el que el cliente elige en el checkout, si Mi Tienda lo
habilita). `order.attended_by_id` guarda ese vendedor y `order.attended_source` quien lo puso ("admin" | "cliente").
`order.seller_id` sigue siendo el encargado de atender el pedido ("Tomar"), sin comision.

Sin relleno: los pedidos de antes quedan sin vendedor que atendio. downgrade quita las columnas.

Revision ID: c7d2e94b1a60
Revises: a7c4e2f91b30
Create Date: 2026-10-22 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7d2e94b1a60"
down_revision: str | None = "a7c4e2f91b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("order") as b:
        b.add_column(sa.Column("attended_by_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("attended_source", sa.String(8), nullable=True))
        b.create_foreign_key("fk_order_attended_by_id", "user", ["attended_by_id"], ["id"])
        b.create_index("ix_order_attended_by_id", ["attended_by_id"])


def downgrade() -> None:
    with op.batch_alter_table("order") as b:
        b.drop_index("ix_order_attended_by_id")
        b.drop_constraint("fk_order_attended_by_id", type_="foreignkey")
        b.drop_column("attended_source")
        b.drop_column("attended_by_id")
