"""snapshot de la tienda publica (store_snapshot)

Guarda el JSON comprimido de la tienda por empresa: productos visibles con campos de tarjeta,
categorias, marcas, tipos y configuracion de vitrina. El endpoint /public/store/{slug}/snapshot
devuelve el blob gz directamente sin ORM, reduciendo la RAM por peticion de ~70 MB a ~1 KB de
lectura de fila mas la transferencia del blob.

Revision ID: f3a8b1c92d45
Revises: c7d2e94b1a60
Create Date: 2026-10-22

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3a8b1c92d45"
down_revision = "c7d2e94b1a60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "store_snapshot",
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(64), nullable=False, server_default=""),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gz", sa.LargeBinary(), nullable=True),
        sa.Column("n_products", sa.Integer(), nullable=True, server_default="0"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )


def downgrade() -> None:
    op.drop_table("store_snapshot")
