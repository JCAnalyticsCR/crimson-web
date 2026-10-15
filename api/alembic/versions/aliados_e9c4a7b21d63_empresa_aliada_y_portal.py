"""aliados: empresa aliada, precios de aliado y asignacion de trabajo a la empresa aliada (portal /aliado)

Revision ID: e9c4a7b21d63
Revises: b4e8c2d61f37
Create Date: 2026-10-14

Agrega:
- ally_company: la empresa aliada es una entidad propia (no un cliente con bandera). Contactos por funcion, estado
  (active / suspended_at), recargo propio, cliente al que se le factura, proveedor al que se le paga, tope de usuarios
  y mensualidad (recurrence_id). Contrato minimo de economia: id, tenant_id, name, active.
- ally_product_price: precio fijo de aliado por producto (general o por empresa), unico por alcance (indice por
  expresion coalesce(ally_company_id, 0)).
- tenant_user.ally_company_id e invitation.ally_company_id: los usuarios de una empresa aliada no entran al panel
  interno (solo a su portal). CHECK ck_tenant_user_un_externo: una membresia no es de un cliente y de un aliado a la vez.
- ally_participation.ally_company_id + visible_to_ally; ally_cost_request.lines/source/responded_by/responded_at.
- survey / work_order / support_ticket / maintenance_contract .ally_company_id (asignacion explicita a la empresa) y
  support_note.share_ally + share_ally_company_id (nota interna compartida con LA empresa aliada que tenia el ticket).

Nada existente cambia de significado: columnas nuevas nulas o con default, sin backfill (las participaciones viejas
por cliente/proveedor siguen igual; se convierten a mano con "Convertir en aliado"). Los roles aliado/tecnico_aliado
los crea sync_roles al arrancar.

Si economia se integro antes y ya creo una ally_company minima, esta migracion no la crea: solo agrega las columnas
que le falten (unica rama condicional, ver _ally_company_existente) y anota en la tabla auxiliar
aliados_e9c4a7b21d63_agregado que le agrego. El downgrade lee esa tabla: en ese caso quita SOLO lo que esta migracion
agrego y deja la tabla de economia (su propio downgrade la borra); sin la tabla auxiliar, la tabla es de esta migracion
y se borra entera.

downgrade: borra las empresas aliadas y sus precios y desliga a sus usuarios. En un entorno real, las membresias con
ally_company_id se desactivan o borran a mano ANTES; en desarrollo quedan con role_code aliado sin empresa y
get_session_principal las rechaza.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e9c4a7b21d63"
down_revision: str | None = "b4e8c2d61f37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# columnas de ally_company ademas del contrato minimo de economia (id, tenant_id, name, active)
_EXTRA = [
    ("legal_name", lambda: sa.Column("legal_name", sa.String(length=200), nullable=True)),
    ("tax_id", lambda: sa.Column("tax_id", sa.String(length=30), nullable=True)),
    ("email", lambda: sa.Column("email", sa.String(length=200), nullable=True)),
    ("phone", lambda: sa.Column("phone", sa.String(length=40), nullable=True)),
    ("contacts", lambda: sa.Column("contacts", sa.JSON(), nullable=True)),
    ("suspended_at", lambda: sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True)),
    ("suspended_reason", lambda: sa.Column("suspended_reason", sa.String(length=240), nullable=True)),
    ("markup_pct", lambda: sa.Column("markup_pct", sa.Numeric(6, 2), nullable=True)),
    ("customer_id", lambda: sa.Column("customer_id", sa.Integer(), nullable=True)),
    ("supplier_id", lambda: sa.Column("supplier_id", sa.Integer(), nullable=True)),
    ("max_users", lambda: sa.Column("max_users", sa.Integer(), nullable=True)),
    ("plan_amount", lambda: sa.Column("plan_amount", sa.Numeric(16, 2), nullable=True)),
    ("plan_currency", lambda: sa.Column("plan_currency", sa.String(length=3), server_default="CRC", nullable=False)),
    ("plan_mode", lambda: sa.Column("plan_mode", sa.String(length=12), server_default="fija", nullable=False)),
    ("plan_since", lambda: sa.Column("plan_since", sa.Date(), nullable=True)),
    ("recurrence_id", lambda: sa.Column("recurrence_id", sa.Integer(), nullable=True)),
    ("notes", lambda: sa.Column("notes", sa.Text(), nullable=True)),
    ("created_by", lambda: sa.Column("created_by", sa.Integer(), nullable=True)),
]
_FKS = [
    ("fk_ally_company_customer", "customer", ["customer_id"], "RESTRICT"),
    ("fk_ally_company_supplier", "supplier", ["supplier_id"], "RESTRICT"),
    ("fk_ally_company_recurrence", "recurrence", ["recurrence_id"], "SET NULL"),
    ("fk_ally_company_created_by", "user", ["created_by"], None),
]

# (tabla, nombre de la FK, ondelete) de las tablas existentes que ganan ally_company_id
_ASIGNABLES = [
    ("ally_participation", "fk_ally_participation_company", "RESTRICT"),
    ("survey", "fk_survey_ally_company", "RESTRICT"),
    ("work_order", "fk_work_order_ally_company", "RESTRICT"),
    ("support_ticket", "fk_support_ticket_ally_company", "RESTRICT"),
    ("maintenance_contract", "fk_contract_ally_company", "RESTRICT"),
]


# tabla auxiliar: solo existe si ally_company ya venia de economia; guarda lo que esta migracion le agrego
_MARCA = "aliados_e9c4a7b21d63_agregado"
_IDX_EXTRA = (
    ("ix_ally_company_name", "name"),
    ("ix_ally_company_tax_id", "tax_id"),
    ("ix_ally_company_active", "active"),
    ("ix_ally_company_customer_id", "customer_id"),
)


def _ally_company_existente() -> bool:
    return sa.inspect(op.get_bind()).has_table("ally_company")


def _create_ally_company() -> None:
    op.create_table(
        "ally_company",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        *[make() for _, make in _EXTRA],
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        *[sa.ForeignKeyConstraint(cols, [f"{tbl}.id"], name=name, ondelete=ond) for name, tbl, cols, ond in _FKS],
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "tax_id", name="uq_ally_company_tax"),
    )
    with op.batch_alter_table("ally_company", schema=None) as b:
        b.create_index(b.f("ix_ally_company_tenant_id"), ["tenant_id"], unique=False)
        b.create_index(b.f("ix_ally_company_name"), ["name"], unique=False)
        b.create_index(b.f("ix_ally_company_tax_id"), ["tax_id"], unique=False)
        b.create_index(b.f("ix_ally_company_active"), ["active"], unique=False)
        b.create_index(b.f("ix_ally_company_customer_id"), ["customer_id"], unique=False)


def _complete_ally_company() -> None:
    """Economia ya creo la tabla minima: se agregan solo las columnas, FK, unico e indices que falten, y se anota que se
    agrego (tabla _MARCA) para que el downgrade quite exactamente eso."""
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("ally_company")}
    idx = {i["name"] for i in insp.get_indexes("ally_company")}
    fks = {f.get("name") for f in insp.get_foreign_keys("ally_company")}
    uqs = {u.get("name") for u in insp.get_unique_constraints("ally_company")}
    agregado: list[dict] = []
    with op.batch_alter_table("ally_company", schema=None) as b:
        for name, make in _EXTRA:
            if name not in cols:
                b.add_column(make())
                agregado.append({"kind": "column", "name": name})
        for name, tbl, fcols, ond in _FKS:
            if name not in fks:
                b.create_foreign_key(name, tbl, fcols, ["id"], ondelete=ond)
                agregado.append({"kind": "fk", "name": name})
        if "uq_ally_company_tax" not in uqs:
            b.create_unique_constraint("uq_ally_company_tax", ["tenant_id", "tax_id"])
            agregado.append({"kind": "unique", "name": "uq_ally_company_tax"})
        for ix, col in _IDX_EXTRA:
            if ix not in idx:
                b.create_index(ix, [col], unique=False)
                agregado.append({"kind": "index", "name": ix})
    marca = op.create_table(
        _MARCA,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
    )
    if agregado:
        op.bulk_insert(marca, agregado)


def _drop_ally_company() -> None:
    """Inverso exacto del paso 1 del upgrade: si la tabla venia de economia (hay _MARCA) quita solo lo agregado aqui;
    si no, la tabla es de esta migracion y se borra entera."""
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_MARCA):
        filas = bind.execute(sa.text(f"SELECT kind, name FROM {_MARCA}")).all()
        por_tipo: dict[str, list[str]] = {}
        for kind, name in filas:
            por_tipo.setdefault(kind, []).append(name)
        with op.batch_alter_table("ally_company", schema=None) as b:
            for ix in por_tipo.get("index", []):
                b.drop_index(ix)
            for uq in por_tipo.get("unique", []):
                b.drop_constraint(uq, type_="unique")
            for fk in por_tipo.get("fk", []):
                b.drop_constraint(fk, type_="foreignkey")
            for col in reversed(por_tipo.get("column", [])):
                b.drop_column(col)
        op.drop_table(_MARCA)
        return
    with op.batch_alter_table("ally_company", schema=None) as b:
        for ix, _col in reversed(_IDX_EXTRA):
            b.drop_index(ix)
        b.drop_index(b.f("ix_ally_company_tenant_id"))
    op.drop_table("ally_company")


def upgrade() -> None:
    # 1. empresa aliada
    if _ally_company_existente():
        _complete_ally_company()
    else:
        _create_ally_company()

    # 2. precios de aliado
    op.create_table(
        "ally_product_price",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("ally_company_id", sa.Integer(), nullable=True),
        sa.Column("price", sa.Numeric(14, 5), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"], name="fk_ally_pp_product", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ally_company_id"], ["ally_company.id"], name="fk_ally_pp_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"], name="fk_ally_pp_updated_by"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ally_product_price", schema=None) as b:
        b.create_index(b.f("ix_ally_product_price_tenant_id"), ["tenant_id"], unique=False)
        b.create_index(b.f("ix_ally_product_price_product_id"), ["product_id"], unique=False)
        b.create_index(b.f("ix_ally_product_price_ally_company_id"), ["ally_company_id"], unique=False)
    op.create_index("uq_ally_product_price_scope", "ally_product_price", ["tenant_id", "product_id", sa.text("coalesce(ally_company_id, 0)")], unique=True)

    # 3. usuarios e invitaciones de la empresa aliada
    with op.batch_alter_table("tenant_user", schema=None) as b:
        b.add_column(sa.Column("ally_company_id", sa.Integer(), nullable=True))
        b.create_index(b.f("ix_tenant_user_ally_company_id"), ["ally_company_id"], unique=False)
        b.create_foreign_key("fk_tenant_user_ally_company", "ally_company", ["ally_company_id"], ["id"], ondelete="RESTRICT")
        b.create_check_constraint("ck_tenant_user_un_externo", "NOT (customer_id IS NOT NULL AND ally_company_id IS NOT NULL)")
    with op.batch_alter_table("invitation", schema=None) as b:
        b.add_column(sa.Column("ally_company_id", sa.Integer(), nullable=True))
        b.create_index(b.f("ix_invitation_ally_company_id"), ["ally_company_id"], unique=False)
        b.create_foreign_key("fk_invitation_ally_company", "ally_company", ["ally_company_id"], ["id"], ondelete="CASCADE")

    # 4. asignacion explicita a la empresa aliada
    for tabla, fk, ond in _ASIGNABLES:
        with op.batch_alter_table(tabla, schema=None) as b:
            b.add_column(sa.Column("ally_company_id", sa.Integer(), nullable=True))
            b.create_index(b.f(f"ix_{tabla}_ally_company_id"), ["ally_company_id"], unique=False)
            b.create_foreign_key(fk, "ally_company", ["ally_company_id"], ["id"], ondelete=ond)
    with op.batch_alter_table("ally_participation", schema=None) as b:
        b.add_column(sa.Column("visible_to_ally", sa.Boolean(), server_default=sa.true(), nullable=False))
    with op.batch_alter_table("ally_cost_request", schema=None) as b:
        b.add_column(sa.Column("lines", sa.JSON(), nullable=True))
        b.add_column(sa.Column("source", sa.String(length=10), server_default="crimson", nullable=False))
        b.add_column(sa.Column("responded_by", sa.Integer(), nullable=True))
        b.add_column(sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True))
        b.create_foreign_key("fk_costreq_responded_by", "user", ["responded_by"], ["id"])
    with op.batch_alter_table("support_note", schema=None) as b:
        b.add_column(sa.Column("share_ally", sa.Boolean(), server_default=sa.false(), nullable=False))
        b.add_column(sa.Column("share_ally_company_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_support_note_share_ally", "ally_company", ["share_ally_company_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("support_note", schema=None) as b:
        b.drop_constraint("fk_support_note_share_ally", type_="foreignkey")
        b.drop_column("share_ally_company_id")
        b.drop_column("share_ally")
    with op.batch_alter_table("ally_cost_request", schema=None) as b:
        b.drop_constraint("fk_costreq_responded_by", type_="foreignkey")
        b.drop_column("responded_at")
        b.drop_column("responded_by")
        b.drop_column("source")
        b.drop_column("lines")
    with op.batch_alter_table("ally_participation", schema=None) as b:
        b.drop_column("visible_to_ally")
    for tabla, fk, _ond in reversed(_ASIGNABLES):
        with op.batch_alter_table(tabla, schema=None) as b:
            b.drop_constraint(fk, type_="foreignkey")
            b.drop_index(b.f(f"ix_{tabla}_ally_company_id"))
            b.drop_column("ally_company_id")
    with op.batch_alter_table("invitation", schema=None) as b:
        b.drop_constraint("fk_invitation_ally_company", type_="foreignkey")
        b.drop_index(b.f("ix_invitation_ally_company_id"))
        b.drop_column("ally_company_id")
    with op.batch_alter_table("tenant_user", schema=None) as b:
        b.drop_constraint("ck_tenant_user_un_externo", type_="check")
        b.drop_constraint("fk_tenant_user_ally_company", type_="foreignkey")
        b.drop_index(b.f("ix_tenant_user_ally_company_id"))
        b.drop_column("ally_company_id")
    op.drop_index("uq_ally_product_price_scope", table_name="ally_product_price")
    with op.batch_alter_table("ally_product_price", schema=None) as b:
        b.drop_index(b.f("ix_ally_product_price_ally_company_id"))
        b.drop_index(b.f("ix_ally_product_price_product_id"))
        b.drop_index(b.f("ix_ally_product_price_tenant_id"))
    op.drop_table("ally_product_price")
    _drop_ally_company()
