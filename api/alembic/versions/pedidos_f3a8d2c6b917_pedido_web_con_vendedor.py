"""pedidos: el pedido web tiene vendedor (reunion 21/10, items 24/42)

`order.seller_id`: el vendedor al que se le asigna el pedido web (lo asigna el admin o el vendedor lo "toma" desde la
lista). La factura que nace del pedido queda con ese vendedor y comisiona como una venta propia (Mi Tienda lo puede
apagar). `order.assigned_at`: cuando se asigno. Sin relleno: los pedidos de antes quedan sin vendedor.

downgrade quita las columnas (las facturas ya creadas conservan su seller_id).

Revision ID: f3a8d2c6b917
Revises: e4c9b1f7a2d6
Create Date: 2026-10-21 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a8d2c6b917"
down_revision: str | None = "e4c9b1f7a2d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("order") as b:
        b.add_column(sa.Column("seller_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
        b.create_foreign_key("fk_order_seller_id", "user", ["seller_id"], ["id"])
        b.create_index("ix_order_seller_id", ["seller_id"])


def downgrade() -> None:
    with op.batch_alter_table("order") as b:
        b.drop_index("ix_order_seller_id")
        b.drop_constraint("fk_order_seller_id", type_="foreignkey")
        b.drop_column("assigned_at")
        b.drop_column("seller_id")
