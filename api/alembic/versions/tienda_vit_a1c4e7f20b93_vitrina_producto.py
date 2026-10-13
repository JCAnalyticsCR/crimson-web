"""tienda_vit: datos de vitrina por producto (precio anterior, ficha tecnica PDF y especificaciones)

La tienda propia reemplaza a Fygaro. Tres columnas opcionales en product, todas nulas por defecto:
- compare_price: precio anterior SIN IVA en la moneda del producto (se muestra tachado si es mayor al actual);
- datasheet_url: enlace a la ficha tecnica PDF del fabricante;
- specs: especificaciones editadas a mano ({"Resolución": "4 MP", ...}); si falta, la vitrina las deduce del nombre.
Nada existente cambia: sin datos, la tienda se ve igual que antes.

Revision ID: a1c4e7f20b93
Revises: d0cb4e92f968
Create Date: 2026-10-12 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4e7f20b93"
down_revision: str | None = "d0cb4e92f968"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("product") as b:
        b.add_column(sa.Column("compare_price", sa.Numeric(14, 5), nullable=True))
        b.add_column(sa.Column("datasheet_url", sa.String(500), nullable=True))
        b.add_column(sa.Column("specs", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("product") as b:
        b.drop_column("specs")
        b.drop_column("datasheet_url")
        b.drop_column("compare_price")
