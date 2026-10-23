"""ventas etapa 2: cartera con dueno (vendedor de la cuenta) y tope de descuento por vendedor

A) customer.account_owner_id: el "Vendedor de la cuenta". El vendedor solo ve sus clientes; admin, jefe de ventas y
   contabilidad ven todos (app/services/cartera.py). No cambia ninguna regla de comisiones.
B) tenant_user.discount_limit_pct: tope de descuento propio de un vendedor. Vacio = el de la empresa.

El rol "jefe_ventas" lo crea sync_roles al arrancar (como los demas roles predefinidos).

Relleno (solo clientes sin dueno, se puede repetir): el vendedor de su factura mas reciente (no anulada); si no tiene,
el responsable de su oportunidad mas reciente; si tampoco, queda NULL ("sin vendedor"). Solo vale alguien que sea
miembro de la misma empresa.

downgrade quita las dos columnas.

Revision ID: c8f4a2d9e631
Revises: e5a9c3b1f270
Create Date: 2026-10-22 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8f4a2d9e631"
down_revision: str | None = "e5a9c3b1f270"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL = (
    """UPDATE customer SET account_owner_id = (
         SELECT i.seller_id FROM invoice i
         WHERE i.customer_id = customer.id AND i.tenant_id = customer.tenant_id AND i.seller_id IS NOT NULL
           AND i.status <> 'anulada'
           AND EXISTS (SELECT 1 FROM tenant_user tu WHERE tu.user_id = i.seller_id AND tu.tenant_id = customer.tenant_id)
         ORDER BY i.issue_date DESC, i.id DESC LIMIT 1)
       WHERE account_owner_id IS NULL""",
    """UPDATE customer SET account_owner_id = (
         SELECT o.owner_id FROM opportunity o
         WHERE o.customer_id = customer.id AND o.tenant_id = customer.tenant_id AND o.owner_id IS NOT NULL
           AND EXISTS (SELECT 1 FROM tenant_user tu WHERE tu.user_id = o.owner_id AND tu.tenant_id = customer.tenant_id)
         ORDER BY o.created_at DESC, o.id DESC LIMIT 1)
       WHERE account_owner_id IS NULL""",
)


def backfill(bind) -> None:
    """Relleno del vendedor de la cuenta (separado para probarlo sin recrear tablas). Solo toca clientes sin dueno."""
    for sql in BACKFILL:
        bind.execute(sa.text(sql))


def upgrade() -> None:
    with op.batch_alter_table("customer") as b:
        b.add_column(sa.Column("account_owner_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_customer_account_owner", "user", ["account_owner_id"], ["id"], ondelete="SET NULL")
        b.create_index("ix_customer_account_owner_id", ["account_owner_id"])
    with op.batch_alter_table("tenant_user") as b:
        b.add_column(sa.Column("discount_limit_pct", sa.Numeric(5, 2), nullable=True))
    backfill(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("tenant_user") as b:
        b.drop_column("discount_limit_pct")
    with op.batch_alter_table("customer") as b:
        b.drop_index("ix_customer_account_owner_id")
        b.drop_constraint("fk_customer_account_owner", type_="foreignkey")
        b.drop_column("account_owner_id")
