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
    # "Lo suyo" en cotizaciones, facturas y cobros = vendedor responsable (services/ownership.py), no quien tecleo.
    "ventas": {
        "dashboard": ["ver"],
        # clientes compartidos: los necesita para cotizar. Crea clientes nuevos (tambien el alta rapida desde la
        # cotizacion) y anota, pero la ficha de un cliente que ya existe es de solo lectura (reunion 21/10, pedido 23:
        # que nadie desvie las notificaciones a otro correo). Editar un cliente: admin y roles con crm.editar.
        "crm": ["ver", "crear", "notas"],
        "crm_pipeline": ["ver", "crear", "editar"],  # oportunidades propias
        "catalog": ["ver", "precios"],
        "sales": ["ver", "crear", "editar", "enviar"],
        "payments": ["ver", "crear"],  # registra el cobro de SUS facturas (la comision nace del cobro)
        # resumen: los levantamientos de SUS oportunidades en solo lectura (13/10 33:30). No los llena: los solicita
        # desde la oportunidad (POST /opportunities/{id}/request-survey). Sin crear ni editar.
        "field": ["ver", "resumen"],
        "commissions": ["ver"],  # solo las suyas: sin ver_todo
        # ajustes de inventario (entradas, salidas y ajustes manuales): solo vendedores y el administrador por ahora
        # (Andres, 19/10). Ve las existencias sin costo; trasladar entre bodegas sigue siendo de bodega.
        "inventory": ["ver", "ajustar"],
    },
    # Contador: todo el dinero de la empresa (gastos, conciliacion, planillas, reportes) sin configurar la empresa.
    # El costo de producto y el margen son del administrador (Ajustes -> Quien ve costos lo puede habilitar).
    # Plan maestro 1.7: opera la facturacion sin el admin. Envia facturas, crea enlaces de pago, emite y registra
    # cobros; NO crea ventas (sin sales.crear ni sales.editar). emitir_fuera ("Marcar emitida en Fygaro", saca la venta
    # de la cola "Por emitir") es solo de contabilidad, la duena de la emision, y del admin: ningun otro rol lo tiene.
    "contabilidad": {
        "dashboard": ["ver", "empresa"],
        "crm": ["ver"],
        "catalog": ["ver", "precios"],
        "sales": ["ver", "ver_todo", "exportar", "recurrencias", "enviar", "emitir_fuera"],
        "payments": ["ver", "exportar", "crear"],
        "accounting": ["ver", "crear", "editar", "exportar"],
        "inventory": ["ver", "exportar"],
        "projects": ["ver", "ver_todo"],
        "purchases": ["ver", "crear", "editar"],
        "payroll": ["ver", "crear", "editar", "aprobar", "configurar"],
        "commissions": ["ver", "ver_todo", "aprobar"],
        "support_desk": ["ver", "ver_todo"],
        "reports": ["ver", "exportar"],
        "vehicles": ["ver", "crear", "editar", "exportar"],
        "approvals": ["ver"],  # aprueba planillas y comisiones
        # Administracion -> Aliados: ve las empresas aliadas y factura su mensualidad (ya tiene sales.recurrencias).
        # Usuarios y precios de aliados quedan para el admin.
        "aliados": ["ver", "mensualidad"],
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
        # garantias: recibe el equipo y hace la revision previa de Crimson (Andres, 19/10)
        "garantias": ["ver", "crear", "editar"],
    },
    # Aliado comercial (12:04-12:32): gerencia de una empresa aliada. Portal propio /aliado; nunca el panel interno.
    # Todo sale filtrado por TenantUser.ally_company_id (services/ally_scope.py). Ningun modulo interno: get_principal
    # lo rechaza igual, pero asi una compuerta que fallara no le abriria nada.
    "aliado": {
        "aliado": [
            "ver",
            "oportunidades",
            "proyectos",
            "precios",
            "levantamientos",
            "levantamientos_crear",
            "asignar",
            "ordenes",
            "tickets",
            "tickets_crear",
            "mantenimientos",
            "activos",
            "catalogo",
            "inventario",
            "ganancias",
            "suscripcion",
            "usuarios",
        ]
    },
    # Tecnico de un aliado (26:03-26:31): "que le toca hacer el dia de hoy". Sin precios, sin activos, sin oportunidades.
    "tecnico_aliado": {"aliado": ["ver", "levantamientos", "ordenes", "tickets"]},
    # Supervisor tecnico: todos los levantamientos, proyectos y ordenes de trabajo; materiales y activos.
    # Revisa y aprueba lo que manda el tecnico; el costeo es del administrador (no ve costos salvo Ajustes).
    "supervisor": {
        "dashboard": ["ver"],
        "crm": ["ver"],
        "crm_pipeline": ["ver", "editar"],
        "catalog": ["ver", "precios"],
        # aprobar_reserva: confirma reservas de material (AC25: ver existencia no es retirar)
        "inventory": ["ver", "crear", "editar", "exportar", "aprobar_reserva"],
        "garantias": ["ver", "crear", "editar"],
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
        "approvals": ["ver"],  # bandeja Aprobaciones: ve las secciones de lo que el ya puede aprobar
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
        "garantias": ["ver"],  # ve donde esta cada equipo de garantia (bodegas de revision y del distribuidor)
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
    # Acceso temporal concedido por el admin (Ajustes -> Acceso de soporte): solo lectura, cada request queda auditado
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
# Usuarios de una empresa aliada: viven en su propio portal (/aliado, routers/ally_portal.py), como los de cliente.
# No se asignan desde Ajustes ni por invitacion suelta: se invitan desde la ficha de su empresa aliada (/allies).
# OJO: models/allies.py ya tiene ALLY_ROLES = papeles de la participacion (contratante, subcontratista...).
ALLY_USER_ROLES = ("aliado", "tecnico_aliado")
EXTERNAL_ROLES = CLIENT_ROLES + ALLY_USER_ROLES
# Lo que se asigna desde Ajustes -> Usuarios: solo roles internos de Crimson. Un rol de cliente sin cliente ligado
# no tiene sentido (y no veria nada); esos se asignan desde la bandeja de accesos del portal del cliente.
ASSIGNABLE_ROLES = [r for r in ROLE_PERMISSIONS if r != "soporte" and r not in EXTERNAL_ROLES]
# Andres 08:07: "eso solo lo puede ver el administrador... solo lo puedo ver yo". Por defecto nadie mas ve costos;
# el admin puede habilitarlo en Ajustes a estos roles y a ningun otro (ventas, caja, tecnicos, aliados: nunca).
COST_ROLE_CHOICES = ("supervisor", "contabilidad", "inventario", "lectura")


# Roles predefinidos cuyos permisos el admin puede cambiar desde Ajustes -> Usuarios -> Roles (reunion 21/10, pedido
# 20). El administrador no se edita (siempre "*"); soporte, clientes y aliados tienen sus propias pantallas.
EDITABLE_ROLES = tuple(r for r in ASSIGNABLE_ROLES if r != "admin")


def role_overrides(tenant) -> dict[str, dict[str, list[str]]]:
    """Lo que la empresa cambio de los roles predefinidos (tenant.settings["role_overrides"]), ya limpio: solo roles
    editables y solo permisos del catalogo, nunca los de solo administrador (core/permisos.py)."""
    from .permisos import clean

    raw = (getattr(tenant, "settings", None) or {}).get("role_overrides") or {}
    if not isinstance(raw, dict):
        return {}
    return {code: clean(perms) for code, perms in raw.items() if code in EDITABLE_ROLES and isinstance(perms, dict)}


def base_permissions(tenant, code: str) -> dict:
    """Permisos de un rol predefinido en esta empresa antes de "Quien ve costos": el ajuste del admin o el del codigo."""
    ov = role_overrides(tenant).get(code)
    return ov if ov is not None else ROLE_PERMISSIONS.get(code, {})


def custom_roles(db: Session, tenant) -> list[Role]:
    """Roles que el admin de esta empresa creo (Ajustes -> Usuarios -> Roles), en orden de creacion."""
    if tenant is None:
        return []
    return list(db.scalars(select(Role).where(Role.tenant_id == tenant.id).order_by(Role.id)))


def assignable_roles(db: Session, tenant) -> list[str]:
    """Roles que se asignan en Ajustes -> Usuarios en esta empresa: los internos predefinidos y los que creo el admin."""
    return [*ASSIGNABLE_ROLES, *(r.code for r in custom_roles(db, tenant))]


def cost_roles(tenant) -> tuple[str, ...]:
    """Roles que, ademas del admin, ven costos en esta empresa (Ajustes). Ignora lo que no sea elegible."""
    raw = (getattr(tenant, "settings", None) or {}).get("cost_roles") or []
    return tuple(r for r in raw if r in COST_ROLE_CHOICES)


def effective_permissions(tenant, code: str, base: dict) -> dict:
    """Permisos del rol mas lo que la empresa cambio en Ajustes: los permisos editados de un rol predefinido (Usuarios ->
    Roles, reemplazan a `base`) y ver costos ("Quien ve costos y margenes").
    Devuelve una COPIA cuando agrega algo: ROLE_PERMISSIONS es compartido por todas las empresas y nunca se muta."""
    ov = role_overrides(tenant).get(code)
    if ov is not None:
        base = ov
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
    ally_company_id: int | None = None  # solo roles aliados: la empresa aliada a la que pertenece
    preview_by: int | None = None  # "Ver como este aliado": id del admin que mira (solo lectura, core/ally.py)

    @property
    def is_client(self) -> bool:
        return self.role in CLIENT_ROLES

    @property
    def is_ally(self) -> bool:
        return self.role in ALLY_USER_ROLES

    @property
    def is_external(self) -> bool:
        return self.is_client or self.is_ally

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
        """Sin "sales.ver_todo" (vendedor) solo ve sus documentos (vendedor responsable, services/ownership.py) y
        sus cobros. "sales.asignar" (elegir el vendedor de un documento) no lo tiene ningun rol: solo el admin ("*")."""
        return self.can("sales", "ver_todo")

    @property
    def sends_quotes(self) -> bool:
        """Enviar una cotizacion (correo o "Marcar enviada") congela su version y pasa la oportunidad del vendedor a
        "enviada": es de quien cotiza (sales.crear o sales.editar), ademas de sales.enviar. Contabilidad tiene
        sales.enviar para mandar FACTURAS (plan 1.7), no las cotizaciones de los vendedores."""
        return self.can("sales", "enviar") and (self.can("sales", "crear") or self.can("sales", "editar"))

    def can(self, module: str, action: str) -> bool:
        return perms_allow(self.permissions, module, action)


def perms_allow(perms: dict, module: str, action: str) -> bool:
    """La misma regla que Principal.can sobre un mapa de permisos suelto ("*" = todo)."""
    if "*" in perms:
        return True
    acts = perms.get(module, [])
    return "*" in acts or action in acts


def _role_permissions(db: Session, code: str) -> dict:
    """Roles predefinidos: manda el codigo (asi los permisos nuevos llegan a empresas ya creadas); lo que la empresa les
    cambio lo aplica effective_permissions. Roles personalizados: los permisos guardados en la tabla role, limpios (nunca
    lo de solo administrador en los creados desde Ajustes; nunca anular ni publicar en ninguno)."""
    if code in ROLE_PERMISSIONS:
        return ROLE_PERMISSIONS[code]
    from .permisos import clean, sin_anular_ni_publicar

    r = db.scalar(select(Role).where(Role.code == code))
    if not r or not r.permissions:
        return {}
    # los creados en Ajustes (con empresa) solo con lo del catalogo; una fila vieja cargada a mano conserva lo suyo,
    # pero ningun rol que no sea el admin anula facturas ni publica en la tienda
    return clean(r.permissions) if r.tenant_id is not None else sin_anular_ni_publicar(r.permissions)


def roles_that_can(db: Session, tenant: Tenant, *needs: tuple[str, str]) -> tuple[str, ...]:
    """Roles internos con miembros activos en esta empresa que tienen TODOS esos permisos (predefinidos y
    personalizados). Para avisos que piden una accion: el aviso le llega solo a quien la puede hacer (plan 1.7)."""
    codes = db.scalars(select(TenantUser.role_code).where(TenantUser.tenant_id == tenant.id, TenantUser.active).distinct()).all()
    out = []
    for code in sorted(set(codes)):
        if code in EXTERNAL_ROLES or code == "soporte":
            continue
        perms = effective_permissions(tenant, code, _role_permissions(db, code))
        if all(perms_allow(perms, m, a) for m, a in needs):
            out.append(code)
    return tuple(out)


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
    # Cerrar sesiones / restablecer contrasena sube session_version: los access tokens emitidos antes mueren aqui.
    # Los tokens de antes del despliegue no traen "sv" y valen como 0 (nadie pierde su sesion por desplegar).
    if int(payload.get("sv", 0) or 0) != int(user.session_version or 0):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesion cerrada: vuelva a iniciar sesion")
    if user.must_change_password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Debe crear una contrasena nueva")
    m = db.scalar(select(TenantUser).where(TenantUser.user_id == user.id, TenantUser.tenant_id == tenant.id, TenantUser.active))
    if not m:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sin acceso a esta empresa")
    # empresa aliada: el rol y la empresa van juntos. Un aliado sin empresa no ve nada; una membresia con empresa
    # aliada y un rol interno es inconsistente y nunca se trata como interna (le abriria todo el panel)
    if m.role_code in ALLY_USER_ROLES and not m.ally_company_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Su cuenta de aliado no está ligada a una empresa aliada. Escríbanos para revisarlo.")
    if m.ally_company_id and m.role_code not in ALLY_USER_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Su acceso no es válido. Escríbanos para revisarlo.")
    # lo mismo con el cliente: una membresia con cliente y un rol interno (dato heredado, arreglo manual) nunca abre el
    # panel interno; y un rol de cliente sin cliente no ve nada
    if (m.customer_id and m.role_code not in CLIENT_ROLES) or (m.role_code in CLIENT_ROLES and not m.customer_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Su acceso no es válido. Escríbanos para revisarlo.")
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
        ally_company_id=m.ally_company_id,
    )


def get_principal(p: Principal = Depends(get_session_principal)) -> Principal:
    """Principal para TODO endpoint interno. Un usuario de cliente o de una empresa aliada nunca pasa de aqui: asi
    ningun endpoint interno (incluidos los que solo piden sesion, como archivo, multimedia o tipo de cambio) le queda
    abierto por olvido. Lo unico que usa get_session_principal es /auth (su cuenta), el portal del cliente (/cliente)
    y el portal de aliados (/aliado, core/ally.py)."""
    if p.is_external or p.ally_company_id is not None or p.customer_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Esta seccion es solo para el equipo de Crimson")
    from . import twofa

    twofa.enforce(p.user, p.tenant, p.role)  # 2FA obligatorio para su rol y la gracia vencida: 403 + X-2FA-Setup
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


def require_one_of(*pairs: tuple[str, str]):
    """Basta uno de los permisos (p. ej. settings.ver o accounting.ver para ver el estado de Hacienda)."""

    def _dep(p: Principal = Depends(get_principal)) -> Principal:
        if not any(p.can(m, a) for m, a in pairs):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Sin permiso: {'|'.join(f'{m}.{a}' for m, a in pairs)}")
        return p

    return _dep
