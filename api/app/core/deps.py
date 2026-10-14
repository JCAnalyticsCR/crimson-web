"""Dependencias: usuario actual, tenant activo y permisos por modulo x accion."""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Role, Tenant, TenantUser, User
from .db import get_db
from .security import decode_token

bearer = HTTPBearer(auto_error=False)

# Roles predefinidos (plan 5.5 + reunion con Andres 13/10). "*" = todo.
# El orden es el que se muestra en Ajustes y en "Ver como": primero los que Andres pidio pulir (32:05).
ROLE_PERMISSIONS: dict[str, dict[str, list[str]]] = {
    # CEO: ve todo lo que se pida (25:02) y es el UNICO que ve el costo (08:07), salvo lo que habilite en Ajustes.
    "admin": {"*": ["*"]},
    # Vendedor (32:25-35:57): sus oportunidades, sus cotizaciones y facturas (buscar solo las suyas), productos con
    # precio de venta, levantamientos como resumen (solo lectura), pedidos de la tienda y sus comisiones.
    # Sin "ver_todo": nunca el total de la empresa. Nunca el costo. El consecutivo es global (services/sequences).
    "ventas": {
        "dashboard": ["ver"],
        "crm": ["ver", "crear", "editar"],  # clientes compartidos: los necesita para cotizar
        "crm_pipeline": ["ver", "crear", "editar"],  # oportunidades propias
        "catalog": ["ver", "precios"],
        "sales": ["ver", "crear", "editar", "enviar"],
        "payments": ["ver", "crear"],  # registra el cobro de SUS facturas (la comision nace del cobro)
        "field": ["ver"],  # resumen de los levantamientos de sus oportunidades; no los llena
        "commissions": ["ver"],  # solo las suyas: sin ver_todo
    },
    # Contador: todo el dinero de la empresa (gastos, conciliacion, planillas, reportes) sin configurar la empresa.
    # El costo de producto y el margen son del administrador (Ajustes -> Quien ve costos lo puede habilitar).
    "contabilidad": {
        "dashboard": ["ver", "empresa"],
        "crm": ["ver"],
        "catalog": ["ver", "precios"],
        "sales": ["ver", "ver_todo", "exportar", "recurrencias"],
        "payments": ["ver", "exportar"],
        "accounting": ["ver", "crear", "editar", "exportar"],
        "inventory": ["ver", "exportar"],
        "projects": ["ver", "ver_todo"],
        "purchases": ["ver", "crear", "editar"],
        "payroll": ["ver", "crear", "editar", "aprobar", "configurar"],
        "commissions": ["ver", "ver_todo", "aprobar"],
        "support_desk": ["ver", "ver_todo"],
        "reports": ["ver", "exportar"],
        "vehicles": ["ver", "crear", "editar", "exportar"],
    },
    # Tecnico instalador de Crimson: solo lo asignado, desde el celular. Nunca ve precios ni costos (22:07-22:23).
    "tecnico": {
        "dashboard": ["ver"],
        "crm": ["ver"],  # buscador de cliente del levantamiento; la seccion no sale en su menu
        "catalog": ["ver"],  # buscador de equipos; sin "precios" el precio sale en 0
        "inventory": ["ver"],
        "field": ["ver", "crear", "editar"],
        "projects": ["ver"],
        "assets": ["ver", "crear", "editar"],
        "support_desk": ["ver", "crear", "editar"],  # solo los suyos: sin ver_todo
    },
    # Aliado comercial (10:31-19:40, 26:36-27:48): ve solo lo que tiene EN COMUN con Crimson. El filtro "en comun"
    # lo pone el frente de alcance aliado; mientras no exista, el rol no es asignable (ASSIGNABLE_ROLES lo excluye).
    # Sin ventas, sin dinero de Crimson, sin ver_todo. "cotizar_aliado" y "precio_aliado" son acciones nuevas que
    # implementan los frentes alcance y precio aliado.
    "aliado": {
        "dashboard": ["ver"],
        "crm": ["ver"],  # clientes en comun (18:27-18:38)
        "crm_pipeline": ["ver", "cotizar_aliado"],  # oportunidades en comun (14:24-14:32); le da sus precios a Crimson (15:37)
        "field": ["ver", "crear", "editar"],  # levantamientos de lo que llevan en comun (14:53, 15:23); OT de sus tecnicos (14:35)
        "projects": ["ver"],  # proyectos aprobados en comun; no cotiza (15:29-15:37)
        "support_desk": ["ver", "editar"],  # tickets asignados a su empresa y mantenimientos en comun (15:48-16:01, 17:18)
        "assets": ["ver"],  # activos de clientes en comun (18:14-18:25)
        "catalog": ["ver", "precio_aliado"],  # todo el catalogo con precio diferenciado (18:39-19:16)
        "inventory": ["ver"],  # solo cantidades (20:01-20:17)
        "commissions": ["ver"],  # lo que gana por proyecto (26:36-27:48)
    },
    # Tecnico de un aliado (26:03-26:31): "solo tiene que ver que le toca hacer el dia de hoy". Levantamientos,
    # soporte y mantenimiento asignados; ni siquiera los activos del cliente. Sin precios.
    "tecnico_aliado": {
        "dashboard": ["ver"],
        "catalog": ["ver"],  # buscador de equipos del levantamiento; sin precios
        "field": ["ver", "crear", "editar"],  # solo lo suyo: sin ver_todo
        "support_desk": ["ver", "editar"],  # tickets y mantenimientos asignados a el
    },
    # Supervisor tecnico: todos los levantamientos, proyectos y ordenes de trabajo; materiales y activos.
    # Revisa y aprueba lo que manda el tecnico; el costeo es del administrador (no ve costos salvo Ajustes).
    "supervisor": {
        "dashboard": ["ver"],
        "crm": ["ver"],
        "crm_pipeline": ["ver", "editar"],
        "catalog": ["ver", "precios"],
        # aprobar_reserva: confirma reservas de material (AC25: ver existencia no es retirar)
        "inventory": ["ver", "crear", "editar", "exportar", "aprobar_reserva"],
        # revisar: aprueba o devuelve con observaciones lo que el tecnico envia, antes del costeo.
        # saltar_revision (cotizar sin revision aprobada) queda solo para admin ("*").
        "field": ["ver", "ver_todo", "crear", "editar", "asignar", "revisar"],
        "projects": ["ver", "ver_todo", "crear", "editar"],
        "assets": ["ver", "crear", "editar"],
        "purchases": ["ver", "crear"],
        "sales": ["ver"],
        "support_desk": ["ver", "ver_todo", "crear", "editar"],
        "reports": ["ver", "exportar"],
        # ve las solicitudes de acceso que le llegan por correo; aprobar (dar acceso a datos) queda para el admin
        "portal_clientes": ["ver"],
        "vehicles": ["ver"],
    },
    # Caja: cobra en mostrador y busca cualquier factura para registrar su pago. Reportes solo de caja.
    "caja": {
        "dashboard": ["ver"],
        "crm": ["ver", "crear"],
        "catalog": ["ver", "precios"],
        "sales": ["ver", "ver_todo", "crear"],
        "payments": ["ver", "crear", "editar"],
        "reports": ["ver", "exportar"],
    },
    # Bodega: catalogo y existencias. Ni ventas ni dinero; reportes solo de inventario. Sin costos (08:07):
    # importar listas de proveedor (cargan costos) queda para el admin.
    "inventario": {
        "dashboard": ["ver"],
        "catalog": ["ver", "precios", "crear", "editar"],
        "inventory": ["ver", "crear", "editar", "exportar"],
        "reports": ["ver", "exportar"],
    },
    # Solo lectura (socio, auditor): ve ventas y cobros de la empresa sin modificar nada. Sin gastos, planillas
    # ni costos (Ajustes -> Quien ve costos lo puede habilitar).
    "lectura": {
        "dashboard": ["ver", "empresa"],
        "crm": ["ver"],
        "crm_pipeline": ["ver"],
        "projects": ["ver", "ver_todo"],
        "assets": ["ver"],
        "catalog": ["ver", "precios"],
        "sales": ["ver", "ver_todo", "exportar"],
        "payments": ["ver"],
        "support_desk": ["ver", "ver_todo"],
        "commissions": ["ver", "ver_todo"],
        "reports": ["ver"],
    },
    # Acceso temporal concedido por el admin (Ajustes -> Soporte): solo lectura, cada request queda auditado
    "soporte": {
        "dashboard": ["ver", "empresa"],
        "crm": ["ver"],
        "catalog": ["ver"],
        "sales": ["ver", "ver_todo"],
        "payments": ["ver"],
        "inventory": ["ver"],
        "accounting": ["ver"],
        "projects": ["ver", "ver_todo"],
        "field": ["ver", "ver_todo"],
        "assets": ["ver"],
        "reports": ["ver"],
        "settings": ["ver"],
    },
    # --- Roles de CLIENTE (portal del cliente). Viven en otro mundo que los internos: solo el modulo "portal",
    # y get_principal los rechaza en cualquier endpoint interno aunque algun dia alguien les sume un permiso.
    # Todo lo que ven sale filtrado en el servidor por el customer_id de su membresia (routers/client_portal.py).
    # cliente_admin: el encargado de la empresa cliente. Tickets de toda su empresa, documentos de dinero
    # (cotizaciones y facturas, solo lectura) y los usuarios de SU empresa.
    "cliente_admin": {"portal": ["ver", "tickets", "comentar_todo", "documentos", "usuarios"]},
    # cliente_usuario: crea y sigue sus tickets; los de su empresa los ve sin poder escribir. Sin dinero.
    "cliente_usuario": {"portal": ["ver", "tickets"]},
}
CLIENT_ROLES = ("cliente_admin", "cliente_usuario")
# Usuarios de una empresa aliada: usan el panel interno, pero todo lo que ven sale filtrado por "en comun".
# No se asignan desde Ajustes ni por invitacion suelta: se dan de alta desde su empresa aliada (frente alcance).
ALLY_ROLES = ("aliado", "tecnico_aliado")
# Lo que se asigna desde Ajustes -> Usuarios: solo roles internos de Crimson. Un rol de cliente sin cliente ligado
# no tiene sentido (y no veria nada); esos se asignan desde la bandeja de accesos del portal del cliente.
ASSIGNABLE_ROLES = [r for r in ROLE_PERMISSIONS if r != "soporte" and r not in CLIENT_ROLES and r not in ALLY_ROLES]
# Andres 08:07: "eso solo lo puede ver el administrador... solo lo puedo ver yo". Por defecto nadie mas ve costos;
# el admin puede habilitarlo en Ajustes a estos roles y a ningun otro (ventas, caja, tecnicos, aliados: nunca).
COST_ROLE_CHOICES = ("supervisor", "contabilidad", "inventario", "lectura")


def cost_roles(tenant) -> tuple[str, ...]:
    """Roles que, ademas del admin, ven costos en esta empresa (Ajustes). Ignora lo que no sea elegible."""
    raw = (getattr(tenant, "settings", None) or {}).get("cost_roles") or []
    return tuple(r for r in raw if r in COST_ROLE_CHOICES)


def effective_permissions(tenant, code: str, base: dict) -> dict:
    """Permisos del rol mas lo que la empresa habilito en Ajustes (hoy: ver costos).
    Devuelve una COPIA cuando agrega algo: ROLE_PERMISSIONS es compartido por todas las empresas y nunca se muta."""
    if "*" in base or "catalog" not in base or code not in cost_roles(tenant):
        return base
    perms = {k: list(v) for k, v in base.items()}
    if "costos" not in perms["catalog"]:
        perms["catalog"].append("costos")
    return perms


@dataclass
class Principal:
    user: User
    tenant: Tenant
    role: str
    permissions: dict[str, list[str]]
    ip: str | None = None
    customer_id: int | None = None  # solo roles de cliente: la empresa cliente a la que pertenece

    @property
    def is_client(self) -> bool:
        return self.role in CLIENT_ROLES

    @property
    def is_ally(self) -> bool:
        return self.role in ALLY_ROLES

    @property
    def sees_field_all(self) -> bool:
        """Sin "field.ver_todo" (tecnico) solo ve sus levantamientos y sus ordenes de trabajo."""
        return self.can("field", "ver_todo")

    @property
    def sees_prices(self) -> bool:
        """Precio de venta. La vendedora lo necesita para cotizar; el tecnico no lo ve."""
        return self.can("catalog", "precios")

    @property
    def sees_costs(self) -> bool:
        """Costo, margen y utilidad: solo el admin (Andres 08:07) y los roles que el admin habilite en Ajustes."""
        return self.can("catalog", "costos")

    @property
    def sees_all_sales(self) -> bool:
        """Sin "sales.ver_todo" (vendedor) solo ve los documentos y pagos que el mismo creo."""
        return self.can("sales", "ver_todo")

    def can(self, module: str, action: str) -> bool:
        p = self.permissions
        if "*" in p:
            return True
        acts = p.get(module, [])
        return "*" in acts or action in acts


def _role_permissions(db: Session, code: str) -> dict:
    """Roles predefinidos: manda el codigo (asi los permisos nuevos llegan a empresas ya creadas).
    Roles personalizados: los permisos guardados en la tabla role."""
    if code in ROLE_PERMISSIONS:
        return ROLE_PERMISSIONS[code]
    r = db.scalar(select(Role).where(Role.code == code))
    return r.permissions if r and r.permissions else {}


def get_session_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No autenticado")
    try:
        payload = decode_token(creds.credentials)
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesion expirada") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token invalido") from e
    if payload.get("typ") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token invalido")
    user = db.get(User, int(payload["sub"]))
    tenant = db.get(Tenant, int(payload["tid"]))
    if not user or not user.active or not tenant or not tenant.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Cuenta inactiva")
    m = db.scalar(select(TenantUser).where(TenantUser.user_id == user.id, TenantUser.tenant_id == tenant.id, TenantUser.active))
    if not m:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sin acceso a esta empresa")
    ip = request.client.host if request.client else None
    if m.role_code == "soporte":
        _support_gate(db, user, tenant, request, ip)
    return Principal(
        user=user,
        tenant=tenant,
        role=m.role_code,
        permissions=effective_permissions(tenant, m.role_code, _role_permissions(db, m.role_code)),
        ip=ip,
        customer_id=m.customer_id,
    )


def get_principal(p: Principal = Depends(get_session_principal)) -> Principal:
    """Principal para TODO endpoint interno. Un usuario de cliente nunca pasa de aqui: asi ningun endpoint
    interno (incluidos los que solo piden sesion, como archivo, multimedia o tipo de cambio) le queda abierto
    por olvido. Lo unico que usa get_session_principal es /auth (su cuenta) y el portal del cliente."""
    if p.is_client:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Esta seccion es solo para el equipo de Crimson")
    return p


def _support_gate(db: Session, user: User, tenant: Tenant, request: Request, ip: str | None) -> None:
    """Soporte: exige concesion vigente y deja constancia de cada request (tambien lecturas)."""
    from datetime import UTC, datetime

    from ..models import AuditLog, SupportGrant

    now = datetime.now(UTC)
    live = db.scalar(
        select(SupportGrant).where(
            SupportGrant.tenant_id == tenant.id, SupportGrant.email == user.email, SupportGrant.revoked_at.is_(None), SupportGrant.expires_at > now
        )
    )
    if not live:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "El acceso de soporte vencio o fue revocado")
    db.add(AuditLog(tenant_id=tenant.id, user_id=user.id, at=now, ip=ip, action=f"soporte {request.method}", entity=request.url.path[:40], entity_id=live.id))
    db.commit()


def require(module: str, action: str):
    def _dep(p: Principal = Depends(get_principal)) -> Principal:
        if not p.can(module, action):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Sin permiso: {module}.{action}")
        return p

    return _dep


def require_any(module: str, *actions: str):
    """Basta una de las acciones (p. ej. seguir o editar): support_desk.editar implica support_desk.seguir."""

    def _dep(p: Principal = Depends(get_principal)) -> Principal:
        if not any(p.can(module, a) for a in actions):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Sin permiso: {module}.{'|'.join(actions)}")
        return p

    return _dep
