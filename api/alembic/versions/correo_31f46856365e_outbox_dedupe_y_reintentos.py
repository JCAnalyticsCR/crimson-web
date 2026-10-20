"""correo: llave de deduplicacion y reintentos con tope en la bitacora de salida

`email_outbox.dedupe_key` (unica por empresa, nula en los correos manuales): el mismo aviso automatico no sale dos
veces. `attempts` y `next_attempt_at`: el reintento espera 15 min x 2^n y a los 6 intentos el correo queda "fallido"
(antes: cada 15 min para siempre). Indice (status, next_attempt_at) para la pasada del worker.
Columnas nuevas, nulas o con valor por defecto: nada existente cambia (las filas viejas quedan con 0 intentos y sin
hora, y el worker las deja de lado si llevan mas de 24 h).

Revision ID: 31f46856365e
Revises: d7e2a9c41f58
Create Date: 2026-10-20 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "31f46856365e"
down_revision: str | None = "d7e2a9c41f58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("email_outbox") as b:
        b.add_column(sa.Column("dedupe_key", sa.String(200), nullable=True))
        b.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        b.create_unique_constraint("uq_email_outbox_dedupe", ["tenant_id", "dedupe_key"])
        b.create_index("ix_email_outbox_retry", ["status", "next_attempt_at"])


def downgrade() -> None:
    with op.batch_alter_table("email_outbox") as b:
        b.drop_index("ix_email_outbox_retry")
        b.drop_constraint("uq_email_outbox_dedupe", type_="unique")
        b.drop_column("next_attempt_at")
        b.drop_column("attempts")
        b.drop_column("dedupe_key")
