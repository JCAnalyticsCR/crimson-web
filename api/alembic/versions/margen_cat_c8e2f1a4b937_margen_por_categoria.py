"""catalogo (10/11): margen por categoria

- category.margin_pct (nueva, nullable): margen sobre la venta definido para la categoria. Lo heredan sus
  subcategorias y los productos sin margen propio. Prioridad: producto -> categoria (la mas cercana) -> general.

Solo agrega la columna vacia: ningun precio cambia al desplegar. Los precios se recalculan unicamente cuando el
administrador confirma el margen de una categoria en Catalogo -> Margenes.

downgrade: borra la columna (los precios ya aplicados se quedan como estan).

Revision ID: c8e2f1a4b937
Revises: a1c4e7b9d203
Create Date: 2026-11-10 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8e2f1a4b937"
down_revision: str | None = "a1c4e7b9d203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("category") as b:
        b.add_column(sa.Column("margin_pct", sa.Numeric(6, 2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("category") as b:
        b.drop_column("margin_pct")
