"""Ventas por asignar (bandeja de facturas sin vendedor, venta de la casa) y cobros por confirmar.

A) Invoice.house_sale: marca "venta de la casa", sin comision.
B) Payment.confirmed_by / confirmed_at / rejection_reason:
   - status "por_confirmar": el vendedor registra el cobro pero no baja el saldo
     ni devenga comision hasta que quien confirma lo apruebe.
   - confirmed_by / confirmed_at: quien y cuando lo aprobo.
   - rejection_reason: motivo si se rechaza (el vendedor lo ve).

Reversible: down() quita las columnas. Sin relleno: filas anteriores quedan con
NULL (house_sale FALSE por defecto en el servidor, confirmed_by NULL idem).

Revision ID: e5a9c3b1f270
Revises: c7d2e94b1a60
Create Date: 2026-10-22 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5a9c3b1f270"
down_revision: str | None = "c7d2e94b1a60"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Invoice: "venta de la casa" — no comisiona aunque se asigne un vendedor despues
    with op.batch_alter_table("invoice") as batch:
        batch.add_column(sa.Column("house_sale", sa.Boolean(), nullable=True, server_default=sa.false()))

    # Payment: confirmacion diferida (vendedor registra, admin/conta confirma)
    with op.batch_alter_table("payment") as batch:
        batch.add_column(sa.Column("confirmed_by", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("rejection_reason", sa.String(300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("payment") as batch:
        batch.drop_column("rejection_reason")
        batch.drop_column("confirmed_at")
        batch.drop_column("confirmed_by")

    with op.batch_alter_table("invoice") as batch:
        batch.drop_column("house_sale")
