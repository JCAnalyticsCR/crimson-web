"""recomendacion: equipo real elegido para una linea generica del levantamiento

Andres (LEV-2026-0008): "ahi deberia dar una recomendacion de cuales camaras podria utilizar". La sugerencia
propone lineas genericas ("Camara Bullet 4 MP"); ahora se puede elegir un producto real para cada una. Cuatro
columnas opcionales en survey_item, todas nulas por defecto:
- supplier_item_id, supplier_cost, supplier_currency: oferta de proveedor elegida sin producto propio (el costeo
  la pasa a colones con el tipo de cambio del dia);
- recommendation: que linea generica cubre y como se eligio (linea, unidades por producto, origen, motivo).
Nada existente cambia: sin datos, el levantamiento se sugiere, costea y cotiza igual que antes.

Revision ID: f6c2a8d14e9b
Revises: a1c4e7f20b93
Create Date: 2026-10-13 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6c2a8d14e9b"
down_revision: str | None = "a1c4e7f20b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("survey_item") as b:
        b.add_column(sa.Column("supplier_item_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("supplier_cost", sa.Numeric(14, 4), nullable=True))
        b.add_column(sa.Column("supplier_currency", sa.String(3), nullable=True))
        b.add_column(sa.Column("recommendation", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("survey_item") as b:
        b.drop_column("recommendation")
        b.drop_column("supplier_currency")
        b.drop_column("supplier_cost")
        b.drop_column("supplier_item_id")
