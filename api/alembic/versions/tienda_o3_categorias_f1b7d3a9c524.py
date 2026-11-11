"""tienda (O3-07a): direccion de categoria guardada, alias con 301 y textos de la pagina de categoria.

Aditiva. category.slug se rellena con EXACTAMENTE la direccion que la tienda calcula hoy (services/store_showcase:
slug del nombre, 60 caracteres, y si dos visibles se llaman igual la segunda lleva el id), asi ninguna /c/ cambia. Las
categorias ocultas reciben el suyo sin chocar con las visibles. category_slug_alias guarda las direcciones viejas
(al renombrar) y responde 301 a la vigente. position, image_url, seo_title, seo_description, h1, intro y guide quedan
vacias (los textos llegan con O3-07b y la aprobacion del dueno).

Revision ID: f1b7d3a9c524
Revises: e8a4c2f61d37
Create Date: 2026-11-11 10:00:00
"""

import re
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1b7d3a9c524"
down_revision: str | None = "e8a4c2f61d37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLS = (
    sa.Column("slug", sa.String(80), nullable=True),
    sa.Column("position", sa.Integer(), nullable=True),
    sa.Column("image_url", sa.String(500), nullable=True),
    sa.Column("seo_title", sa.String(120), nullable=True),
    sa.Column("seo_description", sa.String(200), nullable=True),
    sa.Column("h1", sa.String(120), nullable=True),
    sa.Column("intro", sa.Text(), nullable=True),
    sa.Column("guide", sa.Text(), nullable=True),
)


def _slugify(s: str, limit: int = 60) -> str:
    """copia congelada de services/store_showcase.slugify (la migracion no importa la app: no cambia si la app cambia)"""
    t = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()).strip("-")
    if len(t) > limit:
        t = t[:limit].rsplit("-", 1)[0] or t[:limit]
    return t or "producto"


def upgrade() -> None:
    for col in _COLS:
        op.add_column("category", col)
    op.create_table(
        "category_slug_alias",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("category.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_category_slug_alias"),
    )
    op.create_index("ix_category_slug_alias_tenant_id", "category_slug_alias", ["tenant_id"])
    op.create_index("ix_category_slug_alias_category_id", "category_slug_alias", ["category_id"])

    _backfill(op.get_bind())


def _backfill(bind) -> None:
    """slug de cada categoria = la direccion que la tienda calculaba hoy (la prueba lo compara con el sitemap)."""
    rows = bind.execute(sa.text("SELECT id, tenant_id, name, show_on_web FROM category ORDER BY id")).fetchall()
    by_tenant: dict[int, list] = {}
    for r in rows:
        by_tenant.setdefault(r.tenant_id, []).append(r)
    for cats in by_tenant.values():
        out: dict[int, str] = {}
        seen: set[str] = set()
        # 1) las visibles, igual que hoy (category_slugs sobre las show_on_web, por id)
        for c in cats:
            if c.show_on_web:
                s = _slugify(c.name)
                if s in seen:
                    s = f"{s}-{c.id}"
                seen.add(s)
                out[c.id] = s
        # 2) las ocultas: el suyo, sin chocar con ninguna
        for c in cats:
            if c.id not in out:
                s = _slugify(c.name)
                if s in seen:
                    s = f"{s}-{c.id}"
                seen.add(s)
                out[c.id] = s
        for cid, s in out.items():
            bind.execute(sa.text("UPDATE category SET slug = :s WHERE id = :i"), {"s": s, "i": cid})


def downgrade() -> None:
    op.drop_index("ix_category_slug_alias_category_id", table_name="category_slug_alias")
    op.drop_index("ix_category_slug_alias_tenant_id", table_name="category_slug_alias")
    op.drop_table("category_slug_alias")
    for col in reversed(_COLS):
        op.drop_column("category", col.name)
