"""grupo B: versiones y aceptacion de cotizaciones, CABYS masivo con auditoria, revision del supervisor en levantamientos

Revision ID: c41b7e9d2a60
Revises: c7f2a9e41d38
Create Date: 2026-10-06 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c41b7e9d2a60"
down_revision: str | None = "c7f2a9e41d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- cotizaciones: aceptacion aparte de la emision + versiones inmutables ---
    with op.batch_alter_table("quote", schema=None) as batch_op:
        batch_op.add_column(sa.Column("acceptance_status", sa.String(length=12), nullable=False, server_default="pendiente"))
        batch_op.add_column(sa.Column("accepted_version", sa.Integer(), nullable=True))
        batch_op.create_index("ix_quote_acceptance_status", ["acceptance_status"], unique=False)

    op.create_table(
        "quote_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total", sa.Numeric(16, 5), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("recipient", sa.String(length=200), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_quote_version_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["quote_id"], ["quote.id"], name="fk_quote_version_quote", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sent_by"], ["user.id"], name="fk_quote_version_user"),
        sa.PrimaryKeyConstraint("id", name="pk_quote_version"),
        sa.UniqueConstraint("quote_id", "version", name="uq_quote_version"),
    )
    with op.batch_alter_table("quote_version", schema=None) as batch_op:
        batch_op.create_index("ix_quote_version_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_quote_version_quote_id", ["quote_id"], unique=False)

    op.create_table(
        "quote_acceptance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("contact_name", sa.String(length=160), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=True),
        sa.Column("decided_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_by", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_quote_acceptance_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["quote_id"], ["quote.id"], name="fk_quote_acceptance_quote", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["quote_version.id"], name="fk_quote_acceptance_version"),
        sa.ForeignKeyConstraint(["recorded_by"], ["user.id"], name="fk_quote_acceptance_user"),
        sa.PrimaryKeyConstraint("id", name="pk_quote_acceptance"),
    )
    with op.batch_alter_table("quote_acceptance", schema=None) as batch_op:
        batch_op.create_index("ix_quote_acceptance_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_quote_acceptance_quote_id", ["quote_id"], unique=False)

    # --- productos: quien confirmo el CABYS y cuando ---
    with op.batch_alter_table("product", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cabys_set_by", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cabys_set_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key("fk_product_cabys_set_by", "user", ["cabys_set_by"], ["id"])

    # --- levantamientos: revision del supervisor ---
    with op.batch_alter_table("survey", schema=None) as batch_op:
        batch_op.add_column(sa.Column("review_status", sa.String(length=12), nullable=True))
        batch_op.add_column(sa.Column("reviewed_by", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("review_round", sa.Integer(), nullable=False, server_default="0"))
        batch_op.create_index("ix_survey_review_status", ["review_status"], unique=False)
        batch_op.create_foreign_key("fk_survey_reviewed_by", "user", ["reviewed_by"], ["id"])
    # lo que ya estaba en oficina sin cotizar entra a la cola de revision; lo cotizado/cerrado queda como estaba
    op.execute("UPDATE survey SET review_status = 'pendiente' WHERE status = 'enviado' AND quote_id IS NULL")

    op.create_table(
        "survey_observation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("survey_id", sa.Integer(), nullable=False),
        sa.Column("review_round", sa.Integer(), nullable=False),
        sa.Column("point_code", sa.String(length=20), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Integer(), nullable=True),
        sa.Column("resolution", sa.String(length=400), nullable=True),
        sa.ForeignKeyConstraint(["survey_id"], ["survey.id"], name="fk_survey_observation_survey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], name="fk_survey_observation_created_by"),
        sa.ForeignKeyConstraint(["resolved_by"], ["user.id"], name="fk_survey_observation_resolved_by"),
        sa.PrimaryKeyConstraint("id", name="pk_survey_observation"),
    )
    with op.batch_alter_table("survey_observation", schema=None) as batch_op:
        batch_op.create_index("ix_survey_observation_survey_id", ["survey_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("survey_observation", schema=None) as batch_op:
        batch_op.drop_index("ix_survey_observation_survey_id")
    op.drop_table("survey_observation")

    with op.batch_alter_table("survey", schema=None) as batch_op:
        batch_op.drop_constraint("fk_survey_reviewed_by", type_="foreignkey")
        batch_op.drop_index("ix_survey_review_status")
        batch_op.drop_column("review_round")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by")
        batch_op.drop_column("review_status")

    with op.batch_alter_table("product", schema=None) as batch_op:
        batch_op.drop_constraint("fk_product_cabys_set_by", type_="foreignkey")
        batch_op.drop_column("cabys_set_at")
        batch_op.drop_column("cabys_set_by")

    with op.batch_alter_table("quote_acceptance", schema=None) as batch_op:
        batch_op.drop_index("ix_quote_acceptance_quote_id")
        batch_op.drop_index("ix_quote_acceptance_tenant_id")
    op.drop_table("quote_acceptance")

    with op.batch_alter_table("quote_version", schema=None) as batch_op:
        batch_op.drop_index("ix_quote_version_quote_id")
        batch_op.drop_index("ix_quote_version_tenant_id")
    op.drop_table("quote_version")

    with op.batch_alter_table("quote", schema=None) as batch_op:
        batch_op.drop_index("ix_quote_acceptance_status")
        batch_op.drop_column("accepted_version")
        batch_op.drop_column("acceptance_status")
