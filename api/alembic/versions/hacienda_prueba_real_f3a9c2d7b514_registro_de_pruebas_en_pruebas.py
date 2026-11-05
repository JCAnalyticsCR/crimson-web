"""hacienda (05/11): registro de la «Prueba real con Hacienda» en el ambiente de PRUEBAS

- hacienda_test_run (nueva): cada prueba real lanzada desde Ajustes -> Hacienda (FE de 1 000 colones + IVA de la empresa
  a si misma y, opcional, su mensaje receptor), con el XML firmado, lo que respondio Hacienda y los pasos revisados.
  Sin credenciales, token ni PIN. CHECK environment = 'pruebas' y CHECK amount <= 2000: la tabla no admite otra cosa.
- indice unico parcial (tenant_id) WHERE state IN ('firmado', 'enviado', 'procesando'): una sola prueba viva por
  empresa (igual en el modelo, models/fiscal.py, para que compare_metadata no de diferencias).

downgrade: borra los dos indices y la tabla (se pierde el historial de pruebas; las ventas no se tocan).

Revision ID: f3a9c2d7b514
Revises: b7d4e1c9a382
Create Date: 2026-11-05 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a9c2d7b514"
down_revision: str | None = "b7d4e1c9a382"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIVE_WHERE = "state IN ('firmado', 'enviado', 'procesando')"


def upgrade() -> None:
    op.create_table(
        "hacienda_test_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("environment", sa.String(length=12), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=True),
        sa.Column("cabys", sa.String(length=13), nullable=True),
        sa.Column("amount", sa.Numeric(16, 5), nullable=False),
        sa.Column("con_mr", sa.Boolean(), nullable=False),
        sa.Column("clave", sa.String(length=50), nullable=True),
        sa.Column("consecutive", sa.String(length=20), nullable=True),
        sa.Column("fe_xml", sa.Text(), nullable=True),
        sa.Column("fe_body_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fe_resent", sa.Boolean(), nullable=False),
        sa.Column("fe_location", sa.String(length=300), nullable=True),
        sa.Column("fe_post_cause", sa.String(length=300), nullable=True),
        sa.Column("fe_estado", sa.String(length=16), nullable=True),
        sa.Column("fe_detalle", sa.String(length=500), nullable=True),
        sa.Column("fe_respuesta_xml", sa.Text(), nullable=True),
        sa.Column("mr_consecutive", sa.String(length=20), nullable=True),
        sa.Column("mr_xml", sa.Text(), nullable=True),
        sa.Column("mr_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mr_location", sa.String(length=300), nullable=True),
        sa.Column("mr_estado", sa.String(length=16), nullable=True),
        sa.Column("mr_detalle", sa.String(length=500), nullable=True),
        sa.Column("mr_respuesta_xml", sa.Text(), nullable=True),
        sa.Column("polls", sa.Integer(), nullable=False),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tax_id_at_run", sa.String(length=30), nullable=True),
        sa.Column("cert_sha256", sa.String(length=64), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.CheckConstraint("environment = 'pruebas'", name="ck_hacienda_test_run_pruebas"),
        sa.CheckConstraint("amount <= 2000", name="ck_hacienda_test_run_amount"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("hacienda_test_run") as b:
        b.create_index("ix_hacienda_test_run_tenant_id", ["tenant_id"])
        b.create_index("ix_hacienda_test_run_tenant_created", ["tenant_id", "created_at"])
        b.create_index(
            "uq_hacienda_test_run_live",
            ["tenant_id"],
            unique=True,
            postgresql_where=sa.text(LIVE_WHERE),
            sqlite_where=sa.text(LIVE_WHERE),
        )


def downgrade() -> None:
    with op.batch_alter_table("hacienda_test_run") as b:
        b.drop_index("uq_hacienda_test_run_live")
        b.drop_index("ix_hacienda_test_run_tenant_created")
        b.drop_index("ix_hacienda_test_run_tenant_id")
    op.drop_table("hacienda_test_run")
