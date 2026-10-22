"""roles editables y correo de avisos (reunion 21/10, pedidos 20 y 22)

- role: `tenant_id` (nulo en los predefinidos: admin, ventas, contador...) para los roles que el administrador crea en
  Ajustes -> Usuarios a partir de uno existente, y `base_code` = el rol del que se copio. Los permisos de los roles
  predefinidos siguen en el codigo (ROLE_PERMISSIONS); lo que el admin les cambia vive en tenant.settings
  ("role_overrides") y no necesita columnas.
- tenant_user: `notify_email`, el correo de avisos de esa persona en esa empresa. Si esta, las notificaciones internas
  le llegan ahi en vez de a su correo de entrada (lo de su cuenta: contrasena, invitacion y seguridad, siempre al de
  entrada). Nulo para todos los que ya existen: nada cambia con el despliegue.

Revision ID: f3a8d5c2e917
Revises: e4c9b1f7a2d6
Create Date: 2026-10-21 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a8d5c2e917"
down_revision: str | None = "e4c9b1f7a2d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("role") as b:
        b.add_column(sa.Column("tenant_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("base_code", sa.String(40), nullable=True))
        b.create_foreign_key("fk_role_tenant", "tenant", ["tenant_id"], ["id"], ondelete="CASCADE")
        b.create_index("ix_role_tenant_id", ["tenant_id"])
    with op.batch_alter_table("tenant_user") as b:
        b.add_column(sa.Column("notify_email", sa.String(200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tenant_user") as b:
        b.drop_column("notify_email")
    # un rol personalizado no existe antes de esta revision: las membresias que lo usan vuelven a "lectura" (solo ver)
    # y se borran esas filas; sin esto la llave foranea tenant_user.role_code -> role.code quedaria colgando
    bind = op.get_bind()
    role = sa.table("role", sa.column("code", sa.String), sa.column("tenant_id", sa.Integer))
    tu = sa.table("tenant_user", sa.column("role_code", sa.String))
    inv = sa.table("invitation", sa.column("role_code", sa.String), sa.column("revoked_at", sa.DateTime))
    custom = [r[0] for r in bind.execute(sa.select(role.c.code).where(role.c.tenant_id.is_not(None))).all()]
    if custom:
        bind.execute(tu.update().where(tu.c.role_code.in_(custom)).values(role_code="lectura"))
        bind.execute(inv.update().where(inv.c.role_code.in_(custom)).values(role_code="lectura"))
        bind.execute(role.delete().where(role.c.code.in_(custom)))
    with op.batch_alter_table("role") as b:
        b.drop_index("ix_role_tenant_id")
        b.drop_constraint("fk_role_tenant", type_="foreignkey")
        b.drop_column("base_code")
        b.drop_column("tenant_id")
