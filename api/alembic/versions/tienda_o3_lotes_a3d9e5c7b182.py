"""tienda (O3-11): lotes de contenido de la tienda (borradores, aprobacion, muestra, aplicar y deshacer).

Dos tablas nuevas, nada mas: catalog_batch (el lote) y catalog_draft (cada propuesta por producto y campo, con lo que
habia al aplicar para deshacer exacto). Ningun dato existente cambia.

Revision ID: a3d9e5c7b182
Revises: f1b7d3a9c524
Create Date: 2026-11-11 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3d9e5c7b182"
down_revision: str | None = "f1b7d3a9c524"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_batch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("field", sa.String(30), nullable=False),
        sa.Column("rule", sa.String(40), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("category.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("sample", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_by", sa.Integer(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_by", sa.Integer(), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_catalog_batch_tenant_id", "catalog_batch", ["tenant_id"])
    op.create_index("ix_catalog_batch_status", "catalog_batch", ["status"])
    op.create_table(
        "catalog_draft",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("catalog_batch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("product.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field", sa.String(30), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("previous", sa.JSON(), nullable=True),
        sa.Column("rule", sa.String(40), nullable=False),
        sa.Column("flag", sa.String(120), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_catalog_draft_tenant_id", "catalog_draft", ["tenant_id"])
    op.create_index("ix_catalog_draft_batch_id", "catalog_draft", ["batch_id"])
    op.create_index("ix_catalog_draft_status", "catalog_draft", ["status"])
    op.create_index("ix_catalog_draft_product_field", "catalog_draft", ["product_id", "field"])


def downgrade() -> None:
    op.drop_index("ix_catalog_draft_product_field", table_name="catalog_draft")
    op.drop_index("ix_catalog_draft_status", table_name="catalog_draft")
    op.drop_index("ix_catalog_draft_batch_id", table_name="catalog_draft")
    op.drop_index("ix_catalog_draft_tenant_id", table_name="catalog_draft")
    op.drop_table("catalog_draft")
    op.drop_index("ix_catalog_batch_status", table_name="catalog_batch")
    op.drop_index("ix_catalog_batch_tenant_id", table_name="catalog_batch")
    op.drop_table("catalog_batch")
