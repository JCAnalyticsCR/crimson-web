"""levantamientos: oportunidad propuesta por un aliado (reunion 21/10, item 16)

El aliado puede crear una oportunidad desde su portal "por si a Crimson se le pasa". Queda pendiente de aprobacion de
Crimson (pregunta abierta 8, default propuesto): `opportunity.ally_approval` (NULL = de Crimson | pendiente | aprobada |
rechazada) y `opportunity.ally_proposed_by`, la empresa aliada que la propuso. Sin relleno: las de antes son de
Crimson.

downgrade quita las dos columnas (las oportunidades quedan como de Crimson).

Revision ID: c7e3a9d15f42
Revises: c5d2e8f41a76
Create Date: 2026-10-22 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7e3a9d15f42"
down_revision: str | None = "c5d2e8f41a76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("opportunity") as b:
        b.add_column(sa.Column("ally_approval", sa.String(12), nullable=True))
        b.add_column(sa.Column("ally_proposed_by", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_opportunity_ally_proposed_by", "ally_company", ["ally_proposed_by"], ["id"], ondelete="SET NULL")
        b.create_index("ix_opportunity_ally_approval", ["ally_approval"])


def downgrade() -> None:
    with op.batch_alter_table("opportunity") as b:
        b.drop_index("ix_opportunity_ally_approval")
        b.drop_constraint("fk_opportunity_ally_proposed_by", type_="foreignkey")
        b.drop_column("ally_proposed_by")
        b.drop_column("ally_approval")
