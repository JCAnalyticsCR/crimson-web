"""ventas-control: campo lost_competitor en oportunidad (motivos de perdida cerrados).

Revision ID: e5b3c2d1f408
Revises: 8d3f2e1a4c70
Create Date: 2026-10-22

Reversible: down elimina la columna sin perder otros datos.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e5b3c2d1f408"
down_revision: str | None = "8d3f2e1a4c70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("opportunity", sa.Column("lost_competitor", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("opportunity", "lost_competitor")
