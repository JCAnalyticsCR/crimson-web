"""proveedores: catalogo de proveedores separado del propio (supplier_item) e historial de listas importadas

- supplier_item: lo que ofrece cada proveedor (su codigo, descripcion, modelo extraido, categoria del proveedor,
  existencia y costo). Unico por (tenant, proveedor, codigo). product_id liga con el producto propio (opcional).
- supplier_list_import: cada lista importada (fecha de la lista, archivo, resumen).
Nada existente cambia: product sigue con cost / supplier_stock / supplier_updated_at y la tienda igual.

Revision ID: f2b9d4c7a315
Revises: c5e2a9d47b18
Create Date: 2026-10-13 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2b9d4c7a315"
down_revision: str | None = "c5e2a9d47b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supplier_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("brand", sa.String(length=60), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("model_key", sa.String(length=80), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("subcategory", sa.String(length=120), nullable=True),
        sa.Column("stock", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(14, 4), nullable=True),
        sa.Column("prev_cost", sa.Numeric(14, 4), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("tax_rate", sa.Numeric(6, 3), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("source_file", sa.String(length=200), nullable=True),
        sa.Column("list_date", sa.Date(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("link_method", sa.String(length=12), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_by", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"], name="fk_supplier_item_supplier"),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"], name="fk_supplier_item_product", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "supplier_id", "code", name="uq_supplier_item_code"),
    )
    with op.batch_alter_table("supplier_item", schema=None) as batch_op:
        batch_op.create_index("ix_supplier_item_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_supplier_item_supplier_id", ["supplier_id"], unique=False)
        batch_op.create_index("ix_supplier_item_product_id", ["product_id"], unique=False)
        batch_op.create_index("ix_supplier_item_model_key", ["model_key"], unique=False)

    op.create_table(
        "supplier_list_import",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("list_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("source_file", sa.String(length=200), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_by", sa.Integer(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"], name="fk_supplier_list_import_supplier"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("supplier_list_import", schema=None) as batch_op:
        batch_op.create_index("ix_supplier_list_import_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_supplier_list_import_supplier_id", ["supplier_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("supplier_list_import", schema=None) as batch_op:
        batch_op.drop_index("ix_supplier_list_import_supplier_id")
        batch_op.drop_index("ix_supplier_list_import_tenant_id")
    op.drop_table("supplier_list_import")

    with op.batch_alter_table("supplier_item", schema=None) as batch_op:
        batch_op.drop_index("ix_supplier_item_model_key")
        batch_op.drop_index("ix_supplier_item_product_id")
        batch_op.drop_index("ix_supplier_item_supplier_id")
        batch_op.drop_index("ix_supplier_item_tenant_id")
    op.drop_table("supplier_item")
