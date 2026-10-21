"""contabilidad: factura emitida fuera de la plataforma (cola "Por emitir")

Mientras el modo fiscal es "ninguno", cada venta pagada se emite en Fygaro y se marca aqui como emitida fuera
(einvoice_status = "emitida_fuera", que es texto libre: no cambia el esquema). Columnas nuevas, nulas: las facturas
existentes no cambian.

- invoice.external_number: numero del comprobante en Fygaro.
- invoice.external_date: fecha de emision en Fygaro (cuenta para "emitidas fuera este mes").

Downgrade: el numero y la fecha se copian a las notas internas antes de borrar las columnas (no se pierde el dato) y
el estado "emitida_fuera" se deja tal cual: volver a "sin_emitir" abriria la puerta a reemitir ante Hacienda.

Revision ID: b8e1d4f7a2c6
Revises: e8b3d1f4a627
Create Date: 2026-10-20 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8e1d4f7a2c6"
down_revision: str | None = "e8b3d1f4a627"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("invoice") as b:
        b.add_column(sa.Column("external_number", sa.String(50), nullable=True))
        b.add_column(sa.Column("external_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.execute(
        "UPDATE invoice SET internal_notes = COALESCE(internal_notes || ' · ', '') || 'Emitida fuera de la plataforma: n.º ' || external_number"
        " || COALESCE(' del ' || CAST(external_date AS VARCHAR), '') WHERE external_number IS NOT NULL"
    )
    with op.batch_alter_table("invoice") as b:
        b.drop_column("external_date")
        b.drop_column("external_number")
