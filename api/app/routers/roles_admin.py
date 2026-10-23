"""Ajustes -> Usuarios -> Roles (reunion 21/10, pedido 20: "que Andres pueda definir desde Ajustes que ve cada rol, y
crear roles, sin depender del desarrollador").

Reglas:
- Solo el administrador ve y edita roles (ni un rol con settings.configurar: ese permiso ya no se reparte).
- Los permisos por defecto siguen en el codigo (ROLE_PERMISSIONS). Editar un rol predefinido guarda su version en
  tenant.settings["role_overrides"]; "Restablecer" la borra y vuelve al de fabrica. El administrador no se edita.
- Un rol nuevo se crea a partir de uno existente (copia sus permisos de esta empresa) y vive en la tabla role con
  tenant_id. Se borra solo si nadie lo tiene ni hay invitaciones pendientes con el.
- Lo que hace el rol de origen lo hace el nuevo: lo que el codigo decide por rol (quien recibe ordenes, tickets,
  oportunidades y avisos) usa deps.roles_like; y lo que en Ajustes se marca por rol (2FA obligatorio, regla de «todos
  los vendedores», avisos de pedidos web) nace marcado si el de origen lo estaba (_heredar).
- Un rol editado o creado nunca recibe lo de solo administrador (core/permisos.py: anular facturas, publicar en la
  tienda, ver costos, asignar vendedor, descuento libre, saltar la revision, configurar la empresa): 422.
- Agregarle a un rol (predefinido o creado) algo sensible (Ajustes, contabilidad, planillas, usuarios de aliados o
  aprobar accesos de clientes: core/permisos.SENSITIVE) pide la contrasena del administrador, como asignar un rol asi:
  si no, con una sesion robada se crearia un rol inocente, se asignaria sin contrasena y despues se le sumaria eso.
- Todo cambio queda en la bitacora con el antes y el despues. Aplica en la siguiente peticion de cada persona (los
  permisos se calculan en cada request): no hace falta cerrar sesiones.
"""

from __future__ import annotations

import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.deps import EDITABLE_ROLES, ROLE_PERMISSIONS, Principal, custom_roles, get_principal, role_overrides
from ..core.permisos import CATALOG, LOCKED, clean, expand, problems, sensitive_hits
from ..core.reauth import require_reauth
from ..core.security import MAX_PASSWORD
from ..models import Invitation, Role, Tenant, TenantUser
from ..services.bitacora import audit_change

router = APIRouter(prefix="/settings/roles", tags=["ajustes"])

# nombres visibles de los predefinidos (los mismos de portal/src/app/roles.ts)
LABELS = {
    "admin": "Administrador",
    "ventas": "Vendedor",
    "contabilidad": "Contador",
    "tecnico": "Técnico instalador",
    "supervisor": "Supervisor técnico",
    "caja": "Caja",
    "inventario": "Bodega",
    "lectura": "Solo lectura",
    "jefe_ventas": "Jefe de ventas",
}
MAX_CUSTOM = 30


def solo_admin(p: Principal = Depends(get_principal)) -> Principal:
    if p.role != "admin":
        raise HTTPException(403, "Solo el administrador edita los roles")
    return p


def _members(db: Session, tenant_id: int) -> dict[str, int]:
    rows = db.execute(
        select(TenantUser.role_code, func.count())
        .where(TenantUser.tenant_id == tenant_id, TenantUser.active, TenantUser.customer_id.is_(None), TenantUser.ally_company_id.is_(None))
        .group_by(TenantUser.role_code)
    ).all()
    return {code: n for code, n in rows}


def _role_row(db: Session, code: str) -> Role | None:
    return db.scalar(select(Role).where(Role.code == code))


def _custom(db: Session, p: Principal, code: str) -> Role:
    r = _role_row(db, code)
    if r is None or r.tenant_id != p.tenant.id:
        raise HTTPException(404, "Rol no encontrado")
    return r


def _validate(perms: dict) -> dict[str, list[str]]:
    malos = problems(perms)
    if malos:
        solo_admin_ = [m for m in malos if m in LOCKED or m == "*"]
        if solo_admin_:
            raise HTTPException(422, f"Esto solo lo puede hacer el administrador y no se le da a otro rol: {', '.join(sorted(set(solo_admin_)))}")
        raise HTTPException(422, f"Permisos que no existen: {', '.join(sorted(set(malos)))}")
    out = clean(perms)
    if not out:
        raise HTTPException(422, "El rol necesita al menos un permiso")
    return out


def _out(db: Session, p: Principal, members: dict[str, int]) -> list[dict]:
    ov = role_overrides(p.tenant)
    rows = [
        {
            "code": "admin",
            "name": LABELS["admin"],
            "kind": "predefinido",
            "editable": False,
            "customized": False,
            "base_code": None,
            "permissions": {"*": ["*"]},
            "default_permissions": {"*": ["*"]},
            "members": members.get("admin", 0),
        },
        {
            # ventas etapa 2: no se edita aqui (aprueba hasta sus topes y maneja la cartera); sus topes y si asigna
            # ventas se configuran en Ajustes -> Ventas
            "code": "jefe_ventas",
            "name": LABELS["jefe_ventas"],
            "kind": "predefinido",
            "editable": False,
            "customized": False,
            "base_code": None,
            "permissions": ROLE_PERMISSIONS["jefe_ventas"],
            "default_permissions": ROLE_PERMISSIONS["jefe_ventas"],
            "members": members.get("jefe_ventas", 0),
        },
    ]
    for code in EDITABLE_ROLES:
        rows.append(
            {
                "code": code,
                "name": LABELS.get(code, code.capitalize()),
                "kind": "predefinido",
                "editable": True,
                "customized": code in ov,
                "base_code": None,
                # lo que de verdad aplica hoy, sin "Quien ve costos" (eso se reparte en su propia tarjeta)
                "permissions": ov.get(code, clean(ROLE_PERMISSIONS[code])),
                "default_permissions": clean(ROLE_PERMISSIONS[code]),
                "members": members.get(code, 0),
            }
        )
    for r in custom_roles(db, p.tenant):
        rows.append(
            {
                "code": r.code,
                "name": r.name,
                "kind": "personalizado",
                "editable": True,
                "customized": True,
                "base_code": r.base_code,
                "permissions": clean(r.permissions),
                "default_permissions": None,
                "members": members.get(r.code, 0),
            }
        )
    return rows


@router.get("")
def roles_list(p: Principal = Depends(solo_admin), db: Session = Depends(get_db)):
    return {
        "roles": _out(db, p, _members(db, p.tenant.id)),
        "catalog": [{"module": m, "label": label, "actions": [{"action": a, "label": al} for a, al in acts]} for m, label, acts in CATALOG],
        "locked": sorted(LOCKED),
    }


class RoleEditIn(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=80)
    permissions: dict[str, list[str]] | None = None
    reason: str | None = Field(None, max_length=500)
    # solo si los permisos nuevos agregan algo sensible (_guard_sensitive)
    admin_password: str | None = Field(None, max_length=MAX_PASSWORD)

    @field_validator("name", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


def _guard_sensitive(p: Principal, antes: dict, nuevos: dict, password: str | None) -> None:
    """Sumarle a un rol Ajustes, contabilidad, planillas, usuarios de aliados o aprobar accesos de clientes pide la
    contrasena: todas las personas que ya lo tienen lo ganan sin que nadie mas reautentique. Quitar no la pide."""
    if sensitive_hits(nuevos) - sensitive_hits(antes):
        require_reauth(p, password, "Darle a un rol acceso a Ajustes, contabilidad, planillas, usuarios de aliados o accesos de clientes pide tu contraseña")


@router.put("/{code}")
def role_update(code: str, data: RoleEditIn, p: Principal = Depends(solo_admin), db: Session = Depends(get_db)):
    if code == "admin":
        raise HTTPException(409, "El administrador siempre puede todo: no se edita")
    if code in EDITABLE_ROLES:
        if data.name is not None and data.name != LABELS.get(code):
            raise HTTPException(409, "Los roles predefinidos conservan su nombre: para otro nombre creá un rol nuevo a partir de este")
        if data.permissions is None:
            return {"ok": True}
        nuevos = _validate(data.permissions)
        t = db.get(Tenant, p.tenant.id)
        antes = role_overrides(t).get(code, clean(ROLE_PERMISSIONS[code]))
        _guard_sensitive(p, antes, nuevos, data.admin_password)
        ov = dict((t.settings or {}).get("role_overrides") or {})
        if nuevos == clean(ROLE_PERMISSIONS[code]):
            ov.pop(code, None)  # igual al de fabrica: no se guarda una copia que despues no recibiria los permisos nuevos
        else:
            ov[code] = nuevos
        t.settings = {**(t.settings or {}), "role_overrides": ov}
        r = _role_row(db, code)
        audit_change(db, p, "permissions", "role", r.id if r else None, {"rol": code, "permisos": antes}, {"rol": code, "permisos": nuevos}, reason=data.reason)
        db.commit()
        return {"ok": True, "customized": code in ov}
    if code in ROLE_PERMISSIONS:
        raise HTTPException(409, "Ese rol no se edita desde aquí")
    r = _custom(db, p, code)
    antes = {"nombre": r.name, "permisos": clean(r.permissions)}
    if data.name is not None:
        _unique_name(db, p, data.name, exclude=r.code)
        r.name = data.name
    if data.permissions is not None:
        nuevos = _validate(data.permissions)
        _guard_sensitive(p, antes["permisos"], nuevos, data.admin_password)
        r.permissions = nuevos
    audit_change(db, p, "permissions", "role", r.id, antes, {"nombre": r.name, "permisos": clean(r.permissions)}, reason=data.reason)
    db.commit()
    return {"ok": True, "customized": True}


@router.post("/{code}/reset")
def role_reset(code: str, p: Principal = Depends(solo_admin), db: Session = Depends(get_db)):
    """Vuelve un rol predefinido a sus permisos de fabrica (los del codigo)."""
    if code not in EDITABLE_ROLES:
        raise HTTPException(404, "Solo los roles predefinidos se restablecen")
    t = db.get(Tenant, p.tenant.id)
    ov = dict((t.settings or {}).get("role_overrides") or {})
    if code not in ov:
        return {"ok": True}
    antes = role_overrides(t).get(code, {})
    ov.pop(code)
    t.settings = {**(t.settings or {}), "role_overrides": ov}
    r = _role_row(db, code)
    audit_change(db, p, "permissions", "role", r.id if r else None, {"rol": code, "permisos": antes}, {"rol": code, "permisos": clean(ROLE_PERMISSIONS[code])}, reason="Restablecer")
    db.commit()
    return {"ok": True}


class RoleNewIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    from_code: str = Field(max_length=40)
    permissions: dict[str, list[str]] | None = None  # opcional: si no viene, los del rol de origen

    @field_validator("name", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:24] or "rol"


def _unique_name(db: Session, p: Principal, name: str, exclude: str | None = None) -> None:
    low = name.strip().lower()
    if low in {v.lower() for v in LABELS.values()} | set(ROLE_PERMISSIONS):
        raise HTTPException(409, "Ya hay un rol predefinido con ese nombre")
    if any(r.name.strip().lower() == low and r.code != exclude for r in custom_roles(db, p.tenant)):
        raise HTTPException(409, "Ya hay un rol con ese nombre")


@router.post("", status_code=201)
def role_create(data: RoleNewIn, p: Principal = Depends(solo_admin), db: Session = Depends(get_db)):
    if len(custom_roles(db, p.tenant)) >= MAX_CUSTOM:
        raise HTTPException(409, f"Máximo {MAX_CUSTOM} roles creados por la empresa")
    _unique_name(db, p, data.name)
    if data.from_code in ROLE_PERMISSIONS:
        if data.from_code != "admin" and data.from_code not in EDITABLE_ROLES:
            raise HTTPException(422, "Ese rol no sirve de base: elija uno del equipo interno")
        # el administrador ("*") pasa a todo lo que se puede repartir; los demas, con lo que esta empresa les cambio
        base = expand(ROLE_PERMISSIONS["admin"] if data.from_code == "admin" else role_overrides(p.tenant).get(data.from_code, ROLE_PERMISSIONS[data.from_code]))
    else:
        base = clean(_custom(db, p, data.from_code).permissions)
    perms = _validate(data.permissions) if data.permissions is not None else _validate(base)
    root = f"t{p.tenant.id}_{_slug(data.name)}"
    code, n = root, 1
    while _role_row(db, code) is not None:
        n += 1
        code = f"{root[:36]}_{n}"
    r = Role(code=code, name=data.name, permissions=perms, tenant_id=p.tenant.id, base_code=data.from_code)
    db.add(r)
    db.flush()
    heredado = _heredar(db, p, data.from_code, code)
    audit_change(db, p, "create", "role", r.id, {}, {"nombre": r.name, "a_partir_de": data.from_code, "permisos": perms, "hereda": heredado}, always=True)
    db.commit()
    return {"code": r.code, "name": r.name, "inherited": heredado}


def _heredar(db: Session, p: Principal, origen: str, code: str) -> list[str]:
    """Lo que en Ajustes se decide POR ROL y el rol de origen ya tenia, el nuevo lo trae marcado (el admin lo puede
    desmarcar despues en cada pantalla): la verificacion en dos pasos obligatoria, la regla de comision de «todos los
    vendedores» y los avisos de pedidos web. Asi un «Vendedor senior» hecho a partir de Vendedor comisiona y recibe lo
    mismo, y una copia de Contador sigue exigiendo 2FA. Devuelve que heredo (para la bitacora)."""
    from ..core import twofa
    from ..services.commissions import settings_of as commission_cfg
    from ..services.store_orders import checkout_cfg

    t = db.get(Tenant, p.tenant.id)
    st = dict(t.settings or {})
    out: list[str] = []
    if twofa.is_required(t, origen):
        st["twofa_roles"] = [*dict.fromkeys([*twofa.required_roles(t), code])]
        out.append("2fa_obligatorio")
    generales = list(commission_cfg(t).get("general_rule_roles") or [])
    if origen in generales:
        st["commissions"] = {**(st.get("commissions") or {}), "general_rule_roles": [*generales, code]}
        out.append("regla_general_de_comision")
    avisos = list(checkout_cfg(t).get("notify_roles") or [])
    if origen in avisos:
        store = dict(st.get("store") or {})
        store["checkout"] = {**(store.get("checkout") or {}), "notify_roles": [*avisos, code]}
        st["store"] = store
        out.append("avisos_de_pedidos_web")
    if out:
        t.settings = st
    return out


def _olvidar(db: Session, p: Principal, code: str) -> None:
    """Al borrar un rol creado: sale de las listas por rol de Ajustes (2FA, comisiones, avisos de pedidos web)."""
    t = db.get(Tenant, p.tenant.id)
    st = dict(t.settings or {})
    if code in (st.get("twofa_roles") or []):
        st["twofa_roles"] = [x for x in st["twofa_roles"] if x != code]
    com = dict(st.get("commissions") or {})
    if code in (com.get("general_rule_roles") or []):
        com["general_rule_roles"] = [x for x in com["general_rule_roles"] if x != code]
        st["commissions"] = com
    store = dict(st.get("store") or {})
    ck = dict(store.get("checkout") or {})
    if code in (ck.get("notify_roles") or []):
        ck["notify_roles"] = [x for x in ck["notify_roles"] if x != code]
        store["checkout"] = ck
        st["store"] = store
    t.settings = st


@router.delete("/{code}")
def role_delete(code: str, p: Principal = Depends(solo_admin), db: Session = Depends(get_db)):
    r = _custom(db, p, code)
    usados = db.scalar(select(func.count()).select_from(TenantUser).where(TenantUser.role_code == code)) or 0
    if usados:
        raise HTTPException(409, f"{usados} usuario(s) tienen este rol (activos o no): cámbieles el rol antes de borrarlo")
    pend = db.scalar(select(func.count()).select_from(Invitation).where(Invitation.role_code == code, Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))) or 0
    if pend:
        raise HTTPException(409, "Hay invitaciones pendientes con este rol: revocalas antes de borrarlo")
    audit_change(db, p, "delete", "role", r.id, {"nombre": r.name, "permisos": clean(r.permissions)}, {}, always=True)
    # las invitaciones viejas (aceptadas o revocadas) guardan el codigo como texto, sin llave foranea
    _olvidar(db, p, code)
    db.delete(r)
    db.commit()
    return {"ok": True}
