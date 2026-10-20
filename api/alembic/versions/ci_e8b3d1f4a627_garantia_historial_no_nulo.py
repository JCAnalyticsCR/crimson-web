"""ci: warranty_case.history NOT NULL (deriva entre el modelo y la migracion de garantias)

El modelo declara `history: Mapped[list]` (NOT NULL, por defecto []) y la migracion de garantias la creo nula. Con eso
`alembic check` nunca daba verde y el CI no podia vigilar la deriva de las migraciones que vengan. Los casos sin
historial quedan con [] (lo mismo que el modelo pone al crear uno); despues se marca la columna NOT NULL.

Revision ID: e8b3d1f4a627
Revises: d7e2a9c41f58
Create Date: 2026-10-20 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8b3d1f4a627"
down_revision: str | None = "d7e2a9c41f58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE warranty_case SET history = '[]' WHERE history IS NULL"))
    with op.batch_alter_table("warranty_case") as b:
        b.alter_column("history", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("warranty_case") as b:
        b.alter_column("history", existing_type=sa.JSON(), nullable=True)
