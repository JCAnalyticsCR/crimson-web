"""tienda conectada al inventario: reservas web por pedido, salida por pedido, precio web/destacado y avisos de stock

Revision ID: b7c41e9a2d58
Revises: 9d4e2b7a1c36
Create Date: 2026-10-12 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c41e9a2d58"
down_revision: str | None = "9d4e2b7a1c36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("stock_reservation", schema=None) as batch_op:
        batch_op.add_column(sa.Column("origin", sa.String(length=12), nullable=False, server_default="interna"))
        batch_op.add_column(sa.Column("order_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_stock_reservation_order", "order", ["order_id"], ["id"])
        batch_op.create_index("ix_stock_reservation_order_id", ["order_id"], unique=False)

    with op.batch_alter_table("stock_movement", schema=None) as batch_op:
        batch_op.add_column(sa.Column("order_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_stock_movement_order", "order", ["order_id"], ["id"])
        batch_op.create_index("ix_stock_movement_order_id", ["order_id"], unique=False)

    with op.batch_alter_table("purchase_request", schema=None) as batch_op:
        batch_op.add_column(sa.Column("order_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_purchase_request_order", "order", ["order_id"], ["id"])
        batch_op.create_index("ix_purchase_request_order_id", ["order_id"], unique=False)

    with op.batch_alter_table("product", schema=None) as batch_op:
        batch_op.add_column(sa.Column("web_price", sa.Numeric(14, 5), nullable=True))
        batch_op.add_column(sa.Column("web_featured", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "store_stock_alert",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"], name="fk_store_stock_alert_product", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("store_stock_alert", schema=None) as batch_op:
        batch_op.create_index("ix_store_stock_alert_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_store_stock_alert_product_id", ["product_id"], unique=False)
        batch_op.create_index("ix_store_stock_alert_notified_at", ["notified_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("store_stock_alert", schema=None) as batch_op:
        batch_op.drop_index("ix_store_stock_alert_notified_at")
        batch_op.drop_index("ix_store_stock_alert_product_id")
        batch_op.drop_index("ix_store_stock_alert_tenant_id")
    op.drop_table("store_stock_alert")

    with op.batch_alter_table("product", schema=None) as batch_op:
        batch_op.drop_column("web_featured")
        batch_op.drop_column("web_price")

    with op.batch_alter_table("purchase_request", schema=None) as batch_op:
        batch_op.drop_index("ix_purchase_request_order_id")
        batch_op.drop_constraint("fk_purchase_request_order", type_="foreignkey")
        batch_op.drop_column("order_id")

    with op.batch_alter_table("stock_movement", schema=None) as batch_op:
        batch_op.drop_index("ix_stock_movement_order_id")
        batch_op.drop_constraint("fk_stock_movement_order", type_="foreignkey")
        batch_op.drop_column("order_id")

    with op.batch_alter_table("stock_reservation", schema=None) as batch_op:
        batch_op.drop_index("ix_stock_reservation_order_id")
        batch_op.drop_constraint("fk_stock_reservation_order", type_="foreignkey")
        batch_op.drop_column("order_id")
        batch_op.drop_column("origin")
