"""comercial: tipo de oportunidad (venta | proyecto) y puntos de otro tipo dentro de un levantamiento

Revision ID: b3e8f1a0c2d4
Revises: a7d2c9e41f60
Create Date: 2026-10-05 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e8f1a0c2d4"
down_revision: str | None = "a7d2c9e41f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Las oportunidades que ya existen nacieron todas pensando en instalacion: quedan como "proyecto".
    with op.batch_alter_table("opportunity", schema=None) as batch_op:
        batch_op.add_column(sa.Column("kind", sa.String(length=12), nullable=False, server_default="proyecto"))
    # Punto de otro tipo dentro del levantamiento (una puerta en uno de CCTV). Vacio = el tipo del levantamiento,
    # asi los puntos existentes no cambian.
    with op.batch_alter_table("survey_point", schema=None) as batch_op:
        batch_op.add_column(sa.Column("kind", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("survey_point", schema=None) as batch_op:
        batch_op.drop_column("kind")
    with op.batch_alter_table("opportunity", schema=None) as batch_op:
        batch_op.drop_column("kind")
