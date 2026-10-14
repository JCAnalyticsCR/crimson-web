"""Referencias que llegan en un formulario (cliente, activo, proyecto, usuario...): siempre de ESTA empresa.

Sin esto, quien puede crear o editar recorre ids y la ficha le responde el nombre del cliente, del activo o del usuario
de otra empresa (la salida hace db.get del id guardado), o le manda un correo del ticket a un usuario ajeno.
Se valida antes de gastar un consecutivo. Al editar solo se revisa lo que cambia: un supervisor que ya no esta activo
no impide corregir la prioridad del ticket que tenia.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.deps import CLIENT_ROLES
from ..models import TenantUser


def _changed(data: dict, key: str, current) -> bool:
    v = data.get(key)
    return v is not None and (current is None or getattr(current, key, None) != v)


def check_refs(
    db: Session,
    tenant_id: int,
    data: dict,
    refs: dict[str, tuple[type, str]] | None = None,
    users: dict[str, str] | None = None,
    current=None,
) -> None:
    """refs: campo -> (modelo, mensaje 404) de fichas con tenant_id. users: campo -> mensaje 422 para ids de usuario,
    que deben ser miembros activos del equipo de la empresa (un usuario del portal de clientes no se asigna)."""
    for key, (model, msg) in (refs or {}).items():
        if _changed(data, key, current):
            obj = db.get(model, data[key])
            if not obj or obj.tenant_id != tenant_id:
                raise HTTPException(404, msg)
    for key, msg in (users or {}).items():
        if _changed(data, key, current):
            m = db.scalar(select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == data[key], TenantUser.active))
            if not m or m.role_code in CLIENT_ROLES:
                raise HTTPException(422, msg)
