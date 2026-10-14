"""papelera2: archivo y papelera en mas secciones (tickets, contratos, activos, compras, clientes, productos,
proveedores y cupones)

Andres: "que en cada apartado se puedan borrar". Mismas cuatro columnas que levantamientos y proyectos
(ArchiveMixin). Solo columnas nulas: las filas existentes quedan activas (ni archivadas ni en papelera) sin
backfill. Lo fiscal (facturas, cotizaciones, pagos, notas de credito) y los movimientos de inventario no entran.

Revision ID: c5e2a9d47b18
Revises: a1c4e7f20b93
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "c5e2a9d47b18"
down_revision: str | None = "a1c4e7f20b93"
branch_labels = None
depends_on = None

TABLES = ("support_ticket", "maintenance_contract", "customer_asset", "purchase_request", "customer", "product", "supplier", "coupon")


def upgrade() -> None:
    for t in TABLES:
        with op.batch_alter_table(t, schema=None) as batch_op:
            batch_op.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column("archived_by", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column("trashed_by", sa.Integer(), nullable=True))
            batch_op.create_index(batch_op.f(f"ix_{t}_archived_at"), ["archived_at"], unique=False)
            batch_op.create_index(batch_op.f(f"ix_{t}_trashed_at"), ["trashed_at"], unique=False)


def downgrade() -> None:
    for t in reversed(TABLES):
        with op.batch_alter_table(t, schema=None) as batch_op:
            batch_op.drop_index(batch_op.f(f"ix_{t}_trashed_at"))
            batch_op.drop_index(batch_op.f(f"ix_{t}_archived_at"))
            batch_op.drop_column("trashed_by")
            batch_op.drop_column("trashed_at")
            batch_op.drop_column("archived_by")
            batch_op.drop_column("archived_at")
