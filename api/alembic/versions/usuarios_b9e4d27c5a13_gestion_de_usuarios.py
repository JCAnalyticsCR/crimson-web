"""usuarios: gestion de usuarios en Ajustes (restablecer contrasena, cerrar sesiones, reenviar y revocar invitaciones)

- user: contrasena temporal obligada a cambiarse (must_change_password + temp_password_expires_at, y quien la genero
  y desde que empresa: temp_password_by/temp_password_tenant_id), version de
  sesion (session_version: al subirla mueren los access tokens emitidos) y fecha del ultimo cambio de contrasena.
- invitation: revocada (revoked_at/revoked_by), cuantas veces se envio y cuando fue el ultimo envio.
- password_reset (nueva): enlaces de un solo uso para crear una contrasena nueva; solo se guarda el SHA-256 del token.
  reset_2fa: el admin pidio quitar el 2FA y se quita al usar el enlace.
- email_outbox (solo datos): los correos de invitacion ya guardados llevaban el enlace en claro y el respaldo
  descargable exporta esa tabla. Se borra el token del HTML guardado, igual que hace ahora queue_email con los
  nuevos (el correo real ya salio; lo guardado no sirve para entrar).

Columnas nuevas nulas o con server_default (las tablas user e invitation ya tienen filas): nada existente cambia y
nadie pierde su sesion con el despliegue (los tokens viejos sin version valen como version 0).

Revision ID: b9e4d27c5a13
Revises: c5d18e3f7a2b
Create Date: 2026-10-14 12:00:00
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9e4d27c5a13"
down_revision: str | None = "c5d18e3f7a2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Copia de app/services/mail.py (una migracion no importa codigo de la app, que puede cambiar despues).
_SENSITIVE = ("invitation", "password_reset", "user_security")
_LINK_TOKEN = re.compile(r"(/(?:invitacion|restablecer)/)[A-Za-z0-9_\-]{16,}")


def _redact_outbox() -> None:
    """Borra el token de los enlaces de invitacion/restablecer en el HTML ya guardado de email_outbox."""
    bind = op.get_bind()
    outbox = sa.table("email_outbox", sa.column("id", sa.Integer), sa.column("entity", sa.String), sa.column("html", sa.Text))
    rows = bind.execute(
        sa.select(outbox.c.id, outbox.c.html).where(
            outbox.c.entity.in_(_SENSITIVE), sa.or_(outbox.c.html.like("%/invitacion/%"), outbox.c.html.like("%/restablecer/%"))
        )
    ).all()
    for rid, html in rows:
        limpio = _LINK_TOKEN.sub(r"\1[oculto]", html or "")
        if limpio != html:
            bind.execute(outbox.update().where(outbox.c.id == rid).values(html=limpio))


def upgrade() -> None:
    with op.batch_alter_table("user", schema=None) as b:
        # la tabla user ya tiene filas: las NOT NULL llevan server_default
        b.add_column(sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("temp_password_expires_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("temp_password_by", sa.Integer(), nullable=True))
        b.add_column(sa.Column("temp_password_tenant_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("invitation", schema=None) as b:
        b.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("revoked_by", sa.Integer(), nullable=True))
        b.add_column(sa.Column("sent_count", sa.Integer(), nullable=False, server_default="1"))
        b.add_column(sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "password_reset",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False, server_default="admin"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("reset_2fa", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name="fk_password_reset_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_password_reset_user", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_password_reset_token_hash"),
    )
    with op.batch_alter_table("password_reset", schema=None) as b:
        b.create_index("ix_password_reset_tenant_id", ["tenant_id"], unique=False)
        b.create_index("ix_password_reset_user_id", ["user_id"], unique=False)
    _redact_outbox()


def downgrade() -> None:
    # El token borrado de email_outbox.html no se restaura (no se guardo en ningun lado: era el punto). Esos
    # correos ya salieron; si hace falta, el admin reenvia la invitacion y sale un enlace nuevo.
    with op.batch_alter_table("password_reset", schema=None) as b:
        b.drop_index("ix_password_reset_user_id")
        b.drop_index("ix_password_reset_tenant_id")
    op.drop_table("password_reset")
    with op.batch_alter_table("invitation", schema=None) as b:
        b.drop_column("last_sent_at")
        b.drop_column("sent_count")
        b.drop_column("revoked_by")
        b.drop_column("revoked_at")
    with op.batch_alter_table("user", schema=None) as b:
        b.drop_column("password_changed_at")
        b.drop_column("session_version")
        b.drop_column("temp_password_tenant_id")
        b.drop_column("temp_password_by")
        b.drop_column("temp_password_expires_at")
        b.drop_column("must_change_password")
