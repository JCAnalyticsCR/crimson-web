"""campo: estado por campo del punto, seccion comun del levantamiento y version de plantilla

Revision ID: d5a8c3e17f42
Revises: f3a9c1d2b4e7
Create Date: 2026-10-12 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a8c3e17f42"
down_revision: str | None = "f3a9c1d2b4e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Todo nullable o con valor por defecto: los levantamientos existentes quedan en la plantilla 1, sin estados
    # por campo ni seccion comun, y se siguen calculando exactamente igual.
    with op.batch_alter_table("survey", schema=None) as batch_op:
        batch_op.add_column(sa.Column("common", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("received_by", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("template_version", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("survey_point", schema=None) as batch_op:
        batch_op.add_column(sa.Column("data_status", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("survey_point", schema=None) as batch_op:
        batch_op.drop_column("data_status")
    with op.batch_alter_table("survey", schema=None) as batch_op:
        batch_op.drop_column("template_version")
        batch_op.drop_column("received_by")
        batch_op.drop_column("common")
