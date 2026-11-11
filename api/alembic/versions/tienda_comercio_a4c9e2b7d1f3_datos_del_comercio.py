"""tienda (O1-17): datos del comercio de Crimson en Mi Tienda. Solo datos, sin cambios de esquema.

Llena tenant.settings["store"]["business"] del tenant "crimson" con la razon social, la cedula juridica, el telefono,
el correo y el horario que dio el dueno. SOLO llena los campos que faltan o estan vacios: lo que el dueno ya escribio
en Mi Tienda -> Datos del comercio no se toca, y correrla dos veces da lo mismo (idempotente). Si el tenant no existe
(base nueva, pruebas) no hace nada. La direccion queda vacia a proposito (opcional; el dueno la llena si quiere).

downgrade: no hace nada (no se borran datos que el dueno pudo haber editado despues).

Revision ID: a4c9e2b7d1f3
Revises: b7d3e5f1a924
Create Date: 2026-11-10 20:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4c9e2b7d1f3"
down_revision: str | None = "b7d3e5f1a924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INICIALES = {
    "legal_name": "Grupo Empresarial Crimson SRL",
    "legal_id": "3-102-914922",
    "address": "",
    "phone": "+506 8888-9892",
    "email": "info@crimsoncr.com",
    "hours": "lunes a viernes de 9:00 a. m. a 6:00 p. m.",
}

tenant = sa.table("tenant", sa.column("id", sa.Integer), sa.column("slug", sa.String), sa.column("settings", sa.JSON))


def fill(settings: dict | None) -> dict | None:
    """Los ajustes con los datos iniciales en lo que este vacio; None si no hay nada que cambiar."""
    st = dict(settings or {})
    store = dict(st.get("store") or {})
    biz = dict(store.get("business") or {})
    cambio = False
    for k, v in INICIALES.items():
        if v and not str(biz.get(k) or "").strip():
            biz[k] = v
            cambio = True
        biz.setdefault(k, "")
    if not cambio:
        return None
    store["business"] = biz
    st["store"] = store
    return st


def upgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(sa.select(tenant.c.id, tenant.c.settings).where(tenant.c.slug == "crimson")).first()
    if not row:
        return
    nuevo = fill(row.settings)
    if nuevo is not None:
        bind.execute(tenant.update().where(tenant.c.id == row.id).values(settings=nuevo))


def downgrade() -> None:
    pass
