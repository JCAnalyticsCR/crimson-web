"""margen: guardian de margen en el producto (plan 1.12)

Tres columnas en `product`, todas opcionales o con valor por defecto (expand: nada existente cambia):
- margin_hold (bool, falso): el guardian saco el producto de venta en la tienda ("Consultar") por quedar bajo el
  margen minimo. Nunca cambia el precio: lo propone en Aprobaciones -> "Precios por revisar".
- margin_flagged_at: desde cuando espera revision.
- margin_info (JSON): el ultimo calculo (margen, costo en colones, precio sugerido, salto "dudoso") y el costo de
  referencia con el que se detectan saltos de costo.

Revision ID: b8d3f6a2c917
Revises: b8e1d4f7a2c6
Create Date: 2026-10-20 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8d3f6a2c917"
down_revision: str | None = "b8e1d4f7a2c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("product") as b:
        b.add_column(sa.Column("margin_hold", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("margin_flagged_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("margin_info", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("product") as b:
        b.drop_column("margin_info")
        b.drop_column("margin_flagged_at")
        b.drop_column("margin_hold")
