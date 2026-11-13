"""paquete mensual para la contadora (QuickBooks por archivo): mes revisado y registro del paquete.

- accounting_period.reviewed_at / reviewed_by: quién marcó el mes como "revisado" (estado nuevo, mismo VARCHAR(12)).
- accounting_export.kind: asientos (CSV de asientos, lo de siempre) | paquete. Las filas existentes quedan "asientos".
- accounting_export.manifest: documentos y contactos que salieron en el paquete con su huella (ajustes del siguiente).

Aditiva: columnas nulas o con server_default. Nada existente cambia.

Revision ID: c3f9a7e2d415
Revises: b7c4e1a9d302
Create Date: 2026-11-12 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3f9a7e2d415"
down_revision: str | None = "c5e8f1a2b9d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("accounting_period", schema=None) as b:
        b.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("reviewed_by", sa.Integer(), nullable=True))
    with op.batch_alter_table("accounting_export", schema=None) as b:
        b.add_column(sa.Column("kind", sa.String(length=12), nullable=False, server_default="asientos"))
        b.add_column(sa.Column("manifest", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("accounting_export", schema=None) as b:
        b.drop_column("manifest")
        b.drop_column("kind")
    with op.batch_alter_table("accounting_period", schema=None) as b:
        b.drop_column("reviewed_by")
        b.drop_column("reviewed_at")
