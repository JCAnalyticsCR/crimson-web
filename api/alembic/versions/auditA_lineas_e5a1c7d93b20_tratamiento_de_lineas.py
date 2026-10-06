"""auditoria A: tratamiento de cada linea de cotizacion y factura (normal | pendiente | aportado | cortesia | excluido)

Revision ID: e5a1c7d93b20
Revises: b3e8f1a0c2d4
Create Date: 2026-10-05 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a1c7d93b20"
down_revision: str | None = "b3e8f1a0c2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Lo existente queda "normal": suma como siempre. Las lineas en 0 viejas se resuelven al editarlas.
    for table in ("quote_line", "invoice_line"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("treatment", sa.String(length=12), nullable=False, server_default="normal"))


def downgrade() -> None:
    for table in ("invoice_line", "quote_line"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("treatment")
