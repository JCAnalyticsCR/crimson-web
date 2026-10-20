"""mensualidad: la recurrencia de factura se envia sola, con la primera aprobada por una persona (plan 1.4)

`recurrence.first_run_requires_approval` (verdadero tambien en las recurrencias que ya existen: hasta hoy auto_send no
lo leia nadie, y encenderlo de golpe le mandaria facturas a clientes reales sin que nadie las revise; la proxima
factura de cada una espera "Aprobar y enviar" en Aprobaciones). `last_send_status` y `last_send_note`: resultado del
ultimo envio (por_aprobar | pendiente | enviada | fallida | sin_correo) para la lista de Recurrencias.

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


def downgrade() -> None:
    with op.batch_alter_table("recurrence") as b:
        b.drop_column("last_send_note")
        b.drop_column("last_send_status")
        b.drop_column("first_run_requires_approval")
