"""inventario propio: movimientos enriquecidos, unidades serializadas, reservas formales y fila de control

Revision ID: f3a9c1d2b4e7
Revises: c41b7e9d2a60
Create Date: 2026-10-12 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a9c1d2b4e7"
down_revision: str | None = "c41b7e9d2a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # todo nullable o con defecto: los movimientos y productos existentes quedan exactamente igual
    with op.batch_alter_table("stock_movement", schema=None) as batch_op:
        batch_op.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("serial", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("movement_type", sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column("voucher_media", sa.String(length=400), nullable=True))
        batch_op.add_column(sa.Column("transfer_price", sa.Numeric(14, 5), nullable=True))
        batch_op.add_column(sa.Column("owner", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("custodian", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("location", sa.String(length=160), nullable=True))
        batch_op.create_foreign_key("fk_stock_movement_project", "project", ["project_id"], ["id"])
        batch_op.create_index("ix_stock_movement_project_id", ["project_id"], unique=False)

    with op.batch_alter_table("product", schema=None) as batch_op:
        batch_op.add_column(sa.Column("serialized", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "stock_unit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("serial", sa.String(length=80), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("condition", sa.String(length=24), nullable=False),
        sa.Column("location", sa.String(length=160), nullable=True),
        sa.Column("owner", sa.String(length=160), nullable=True),
        sa.Column("custodian", sa.String(length=160), nullable=True),
        sa.Column("customer_asset_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"], name="fk_stock_unit_product"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouse.id"], name="fk_stock_unit_warehouse"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_stock_unit_project"),
        sa.ForeignKeyConstraint(["customer_asset_id"], ["customer_asset.id"], name="fk_stock_unit_asset"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], name="fk_stock_unit_created_by"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_stock_unit_tenant", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "serial", name="uq_stock_unit_serial"),
    )
    with op.batch_alter_table("stock_unit", schema=None) as batch_op:
        batch_op.create_index("ix_stock_unit_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_stock_unit_product_id", ["product_id"], unique=False)
        batch_op.create_index("ix_stock_unit_status", ["status"], unique=False)
        batch_op.create_index("ix_stock_unit_project_id", ["project_id"], unique=False)

    op.create_table(
        "stock_reservation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("consumed_quantity", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("survey_id", sa.Integer(), nullable=True),
        sa.Column("quote_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("closed_note", sa.String(length=300), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"], name="fk_stock_reservation_product"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouse.id"], name="fk_stock_reservation_warehouse"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_stock_reservation_project"),
        sa.ForeignKeyConstraint(["survey_id"], ["survey.id"], name="fk_stock_reservation_survey"),
        sa.ForeignKeyConstraint(["quote_id"], ["quote.id"], name="fk_stock_reservation_quote"),
        sa.ForeignKeyConstraint(["requested_by"], ["user.id"], name="fk_stock_reservation_requested_by"),
        sa.ForeignKeyConstraint(["approved_by"], ["user.id"], name="fk_stock_reservation_approved_by"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_stock_reservation_tenant", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("stock_reservation", schema=None) as batch_op:
        batch_op.create_index("ix_stock_reservation_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_stock_reservation_product_id", ["product_id"], unique=False)
        batch_op.create_index("ix_stock_reservation_warehouse_id", ["warehouse_id"], unique=False)
        batch_op.create_index("ix_stock_reservation_project_id", ["project_id"], unique=False)
        batch_op.create_index("ix_stock_reservation_status", ["status"], unique=False)
        batch_op.create_index("ix_stock_reservation_expires_at", ["expires_at"], unique=False)

    op.create_table(
        "stock_lock",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"], name="fk_stock_lock_product"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouse.id"], name="fk_stock_lock_warehouse"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_stock_lock_tenant", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "product_id", "warehouse_id", name="uq_stock_lock"),
    )
    with op.batch_alter_table("stock_lock", schema=None) as batch_op:
        batch_op.create_index("ix_stock_lock_tenant_id", ["tenant_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("stock_lock", schema=None) as batch_op:
        batch_op.drop_index("ix_stock_lock_tenant_id")
    op.drop_table("stock_lock")

    with op.batch_alter_table("stock_reservation", schema=None) as batch_op:
        for ix in ("expires_at", "status", "project_id", "warehouse_id", "product_id", "tenant_id"):
            batch_op.drop_index(f"ix_stock_reservation_{ix}")
    op.drop_table("stock_reservation")

    with op.batch_alter_table("stock_unit", schema=None) as batch_op:
        for ix in ("project_id", "status", "product_id", "tenant_id"):
            batch_op.drop_index(f"ix_stock_unit_{ix}")
    op.drop_table("stock_unit")

    with op.batch_alter_table("product", schema=None) as batch_op:
        batch_op.drop_column("serialized")

    with op.batch_alter_table("stock_movement", schema=None) as batch_op:
        batch_op.drop_index("ix_stock_movement_project_id")
        batch_op.drop_constraint("fk_stock_movement_project", type_="foreignkey")
        batch_op.drop_column("location")
        batch_op.drop_column("custodian")
        batch_op.drop_column("owner")
        batch_op.drop_column("transfer_price")
        batch_op.drop_column("voucher_media")
        batch_op.drop_column("movement_type")
        batch_op.drop_column("serial")
        batch_op.drop_column("project_id")
