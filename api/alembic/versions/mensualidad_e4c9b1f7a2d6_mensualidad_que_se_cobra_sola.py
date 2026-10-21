"""mensualidad: la recurrencia de factura se envia sola, con la primera aprobada por una persona (plan 1.4)

`recurrence.first_run_requires_approval` (verdadero tambien en las recurrencias que ya existen: hasta hoy auto_send no
lo leia nadie, y encenderlo de golpe le mandaria facturas a clientes reales sin que nadie las revise; la proxima
factura de cada una espera "Aprobar y enviar" en Aprobaciones). `last_send_status` y `last_send_note`: resultado del
ultimo envio (por_aprobar | enviando | pendiente | enviada | fallida | sin_correo) para la lista de Recurrencias.
`invoice.recurrence_id`: la recurrencia que genero la factura (sin comprobante fiscal aceptado sale como "aviso de
cobro"). Sin relleno: lo que ya salio como "Factura" conserva su titulo.

Revision ID: e4c9b1f7a2d6
Revises: c0b7a3e5d912
Create Date: 2026-10-20 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4c9b1f7a2d6"
down_revision: str | None = "c0b7a3e5d912"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("recurrence") as b:
        b.add_column(sa.Column("first_run_requires_approval", sa.Boolean(), nullable=False, server_default=sa.true()))
        b.add_column(sa.Column("last_send_status", sa.String(12), nullable=True))
        b.add_column(sa.Column("last_send_note", sa.String(240), nullable=True))
    with op.batch_alter_table("invoice") as b:
        b.add_column(sa.Column("recurrence_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_invoice_recurrence", "recurrence", ["recurrence_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("invoice") as b:
        b.drop_constraint("fk_invoice_recurrence", type_="foreignkey")
        b.drop_column("recurrence_id")
    with op.batch_alter_table("recurrence") as b:
        b.drop_column("last_send_note")
        b.drop_column("last_send_status")
        b.drop_column("first_run_requires_approval")
