"""portal del cliente: usuarios ligados a un cliente, invitaciones de cliente y solicitudes de acceso

Revision ID: b3e8f1a2c9d4
Revises: a7d2c9e41f60
Create Date: 2026-10-05

- tenant_user.customer_id: nulo para el equipo interno; con valor = usuario del portal de ESE cliente.
- invitation.customer_id: la invitacion al portal lleva el cliente; al aceptarla la membresia queda ligada.
- access_request: lo que llega desde la pagina publica "Solicitar acceso". Es solo una solicitud.
Columnas nuevas nulas (sin backfill). Las FK van con nombre y dentro de batch_alter_table para SQLite.
Los roles cliente_admin / cliente_usuario los crea sync_roles al arrancar (igual que los demas roles).
"""

import sqlalchemy as sa
from alembic import op

revision = "b3e8f1a2c9d4"
down_revision: str | None = "a7d2c9e41f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenant_user", schema=None) as b:
        b.add_column(sa.Column("customer_id", sa.Integer(), nullable=True))
        b.create_index(b.f("ix_tenant_user_customer_id"), ["customer_id"], unique=False)
        b.create_foreign_key("fk_tenant_user_customer_id", "customer", ["customer_id"], ["id"], ondelete="RESTRICT")

    with op.batch_alter_table("invitation", schema=None) as b:
        b.add_column(sa.Column("customer_id", sa.Integer(), nullable=True))
        b.create_index(b.f("ix_invitation_customer_id"), ["customer_id"], unique=False)
        b.create_foreign_key("fk_invitation_customer_id", "customer", ["customer_id"], ["id"], ondelete="CASCADE")

    op.create_table(
        "access_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=25), nullable=True),
        sa.Column("company", sa.String(length=160), nullable=True),
        sa.Column("id_number", sa.String(length=30), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=12), server_default="pendiente", nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("role_code", sa.String(length=40), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invitation_id", sa.Integer(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["invitation_id"], ["invitation.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("access_request", schema=None) as b:
        b.create_index(b.f("ix_access_request_tenant_id"), ["tenant_id"], unique=False)
        b.create_index(b.f("ix_access_request_email"), ["email"], unique=False)
        b.create_index(b.f("ix_access_request_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("access_request", schema=None) as b:
        b.drop_index(b.f("ix_access_request_status"))
        b.drop_index(b.f("ix_access_request_email"))
        b.drop_index(b.f("ix_access_request_tenant_id"))
    op.drop_table("access_request")
    with op.batch_alter_table("invitation", schema=None) as b:
        b.drop_constraint("fk_invitation_customer_id", type_="foreignkey")
        b.drop_index(b.f("ix_invitation_customer_id"))
        b.drop_column("customer_id")
    with op.batch_alter_table("tenant_user", schema=None) as b:
        b.drop_constraint("fk_tenant_user_customer_id", type_="foreignkey")
        b.drop_index(b.f("ix_tenant_user_customer_id"))
        b.drop_column("customer_id")
