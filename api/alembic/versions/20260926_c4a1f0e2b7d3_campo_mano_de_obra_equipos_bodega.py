"""campo: mano de obra por tipo, equipos vs materiales, costo en el costeo, quien envio y bodega del producto

Revision ID: c4a1f0e2b7d3
Revises: cccf260a87ac
Create Date: 2026-09-26 09:00:00
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a1f0e2b7d3"
down_revision: str | None = "cccf260a87ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("survey", schema=None) as batch_op:
        batch_op.add_column(sa.Column("visit_tech_ids", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("labor", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("sent_by", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_survey_sent_by", "user", ["sent_by"], ["id"])

    with op.batch_alter_table("survey_item", schema=None) as batch_op:
        # la tabla ya tiene filas: NOT NULL necesita server_default
        batch_op.add_column(sa.Column("kind", sa.String(length=10), nullable=False, server_default="material"))
        batch_op.add_column(sa.Column("unit_cost", sa.Numeric(14, 4), nullable=True))

    with op.batch_alter_table("product", schema=None) as batch_op:
        batch_op.add_column(sa.Column("warehouse_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_product_warehouse", "warehouse", ["warehouse_id"], ["id"])
        batch_op.create_index("ix_product_warehouse_id", ["warehouse_id"], unique=False)

    # Levantamientos existentes: el viejo "tecnicos x dias" pasa a ser el personal tecnico requerido.
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, techs, days FROM survey")).fetchall()
    for sid, techs, days in rows:
        labor = {
            "tecnico": {"people": int(techs or 0), "days": str(days or 0)},
            "civil": {"people": 0, "days": "0"},
            "contratado": {"people": 0, "days": "0"},
        }
        bind.execute(sa.text("UPDATE survey SET labor = :l, visit_tech_ids = :v WHERE id = :i"), {"l": json.dumps(labor), "v": "[]", "i": sid})


def downgrade() -> None:
    with op.batch_alter_table("product", schema=None) as batch_op:
        batch_op.drop_index("ix_product_warehouse_id")
        batch_op.drop_constraint("fk_product_warehouse", type_="foreignkey")
        batch_op.drop_column("warehouse_id")
    with op.batch_alter_table("survey_item", schema=None) as batch_op:
        batch_op.drop_column("unit_cost")
        batch_op.drop_column("kind")
    with op.batch_alter_table("survey", schema=None) as batch_op:
        batch_op.drop_constraint("fk_survey_sent_by", type_="foreignkey")
        batch_op.drop_column("sent_by")
        batch_op.drop_column("labor")
        batch_op.drop_column("visit_tech_ids")
