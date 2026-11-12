"""multimedia en bucket: media.storage (db | s3) y media.data opcional.

Aditiva y sin reescribir la tabla:
- media.storage VARCHAR(4) NOT NULL DEFAULT 'db': en Postgres 11+ agregar una columna con valor por defecto constante no
  reescribe la tabla (226 MB de bytea). Todas las filas existentes quedan "db": nada cambia hasta que se copie.
- media.data pasa a admitir NULL (solo quita la restriccion; el dato no se toca). Lo vacian unicamente
  `app.tools.media_al_bucket liberar --si` (filas ya copiadas y verificadas) y las subidas nuevas con MEDIA_STORAGE=s3.
size y sha256 ya existian (sesion 4).

Bajar: solo si ninguna fila depende del bucket (correr antes `media_al_bucket revertir`); si no, se niega en vez de
dejar archivos inaccesibles.

Revision ID: b7c4e1a9d302
Revises: a3d9e5c7b182
Create Date: 2026-11-11 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c4e1a9d302"
down_revision: str | None = "a3d9e5c7b182"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media", schema=None) as b:
        b.add_column(sa.Column("storage", sa.String(length=4), server_default="db", nullable=False))
        b.alter_column("data", existing_type=sa.LargeBinary(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    sin_bytes = bind.execute(sa.text("SELECT COUNT(*) FROM media WHERE data IS NULL")).scalar_one()
    if sin_bytes:
        raise RuntimeError(
            f"{sin_bytes} archivos de multimedia viven solo en el bucket: corra `python -m app.tools.media_al_bucket revertir` antes de bajar esta migracion"
        )
    with op.batch_alter_table("media", schema=None) as b:
        b.alter_column("data", existing_type=sa.LargeBinary(), nullable=False)
        b.drop_column("storage")
