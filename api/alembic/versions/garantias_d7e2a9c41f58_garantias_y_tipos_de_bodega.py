"""garantias: casos de garantia con revision previa de Crimson y tipos de bodega

Andres (19/10): las garantias pasan primero por una revision de Crimson y despues se mandan al distribuidor, que tiene
taller especializado. Para seguir el equipo en el kardex hacen falta bodegas que no se venden: `warehouse.kind`
(venta | revision | proveedor; todas las existentes quedan "venta", nada cambia para ellas). Tabla nueva
`warranty_case` y `stock_movement.warranty_id` (nulo: los movimientos existentes no cambian).

Revision ID: d7e2a9c41f58
Revises: f1c7a9d3e520
Create Date: 2026-10-19 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7e2a9c41f58"
down_revision: str | None = "f1c7a9d3e520"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("warehouse") as b:
        b.add_column(sa.Column("kind", sa.String(12), nullable=False, server_default="venta"))

    op.create_table(
        "warranty_case",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("number", sa.String(40), nullable=False, index=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customer.id"), nullable=False, index=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("support_ticket.id"), nullable=True, index=True),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("customer_asset.id"), nullable=True, index=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("product.id"), nullable=True),
        sa.Column("equipment", sa.String(200), nullable=False),
        sa.Column("serial", sa.String(80), nullable=True, index=True),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False, server_default="1"),
        sa.Column("problem", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="recibido", index=True),
        sa.Column("review", sa.String(12), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("supplier.id"), nullable=True),
        sa.Column("supplier_ref", sa.String(80), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(12), nullable=True),
        sa.Column("new_serial", sa.String(80), nullable=True),
        sa.Column("replacement_product_id", sa.Integer(), sa.ForeignKey("product.id"), nullable=True),
        sa.Column("replacement_qty", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("replacement_serial", sa.String(80), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closing_note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("history", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    with op.batch_alter_table("stock_movement") as b:
        b.add_column(sa.Column("warranty_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_stock_movement_warranty", "warranty_case", ["warranty_id"], ["id"])
        b.create_index("ix_stock_movement_warranty_id", ["warranty_id"])


def downgrade() -> None:
    with op.batch_alter_table("stock_movement") as b:
        b.drop_index("ix_stock_movement_warranty_id")
        b.drop_constraint("fk_stock_movement_warranty", type_="foreignkey")
        b.drop_column("warranty_id")
    op.drop_table("warranty_case")
    with op.batch_alter_table("warehouse") as b:
        b.drop_column("kind")
