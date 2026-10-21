"""seguridad (plan 1.10): 2FA que no se apaga con solo la sesion, codigos que no se repiten y 2FA obligatorio

- user.totp_pending_secret: el secreto nuevo de /auth/2fa/setup espera aqui hasta que /auth/2fa/verify lo confirma;
  el 2FA anterior sigue activo mientras tanto (antes setup lo apagaba con solo la sesion).
- user.totp_last_step: ultima ventana de 30 s ya usada; un codigo de esa ventana o anterior no se acepta otra vez.
- user.twofa_required_since: primera vez que su rol le exigio 2FA (Ajustes -> Usuarios); desde ahi corren los dias de
  gracia de la empresa.

Columnas nulas: nada existente cambia, nadie pierde su sesion y el 2FA activo de cada cuenta sigue igual.

Revision ID: c3f8a1d6e924
Revises: b8d3f6a2c917
Create Date: 2026-10-20 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3f8a1d6e924"
down_revision: str | None = "b8d3f6a2c917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user") as b:
        b.add_column(sa.Column("totp_pending_secret", sa.String(64), nullable=True))
        b.add_column(sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
        b.add_column(sa.Column("twofa_required_since", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user") as b:
        b.drop_column("twofa_required_since")
        b.drop_column("totp_last_step")
        b.drop_column("totp_pending_secret")
