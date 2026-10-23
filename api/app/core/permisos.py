"""Catalogo de permisos que el administrador puede repartir en Ajustes -> Usuarios -> Roles (reunion 21/10, pedido 20).

Solo datos (sin imports de la app): core/deps.py lo usa para aplicar lo que la empresa cambio y routers/roles_admin.py
para validar. Los permisos por defecto de cada rol siguen en ROLE_PERMISSIONS (core/deps.py).

Lo que NUNCA recibe un rol editado o creado (solo el administrador, "*"):
- sales.anular (anular facturas y cotizaciones, pedido 18) y catalog.publicar (publicar en la tienda): ya hay pruebas
  que lo exigen (tests/test_anular_solo_admin.py, tests/test_publicar_solo_admin.py).
- catalog.costos: el costo se reparte solo en "Quien ve costos y margenes" (y nunca a ventas, caja ni tecnicos).
- sales.asignar (elegir el vendedor de un documento), sales.descuento_libre (descontar sin aprobacion),
  sales.aprobar (aprobar cotizaciones retenidas por descuento o margen: dado a un vendedor, aprobaria las suyas y el
  tope de descuento y el margen minimo quedarian sin efecto) y field.saltar_revision (cotizar sin revision aprobada):
  decisiones del administrador.
- settings.configurar: configurar la empresa, usuarios, pasarelas y Hacienda es del administrador.
"""

from __future__ import annotations

# (modulo, nombre visible, [(accion, nombre visible), ...]) en el orden del menu
CATALOG: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("dashboard", "Inicio", [("ver", "Ver su inicio"), ("empresa", "Números de toda la empresa")]),
    ("crm", "Clientes", [("ver", "Ver"), ("crear", "Crear clientes nuevos"), ("notas", "Agregar notas"), ("editar", "Editar clientes existentes")]),
    ("crm_pipeline", "Oportunidades", [("ver", "Ver"), ("ver_todo", "Ver las de todos"), ("crear", "Crear"), ("editar", "Editar")]),
    ("sales", "Cotizaciones y facturas", [
        ("ver", "Ver"), ("ver_todo", "Ver las de toda la empresa"), ("crear", "Crear"), ("editar", "Editar"), ("enviar", "Enviar"),
        ("exportar", "Exportar"), ("recurrencias", "Recurrencias"),
        ("emitir_fuera", "Marcar emitidas en Fygaro"),
        ("asignar_venta", "Asignar vendedor a ventas sin asignar"),
    ]),
    ("payments", "Pagos", [("ver", "Ver"), ("crear", "Registrar cobros"), ("editar", "Editar cobros"), ("exportar", "Exportar"), ("confirmar", "Confirmar cobros del vendedor")]),
    ("catalog", "Productos y servicios", [("ver", "Ver"), ("precios", "Ver precio de venta"), ("crear", "Crear"), ("editar", "Editar")]),
    ("inventory", "Inventario", [
        ("ver", "Ver existencias"), ("ajustar", "Ajustes de inventario"), ("crear", "Movimientos"), ("editar", "Editar"),
        ("aprobar_reserva", "Aprobar reservas"), ("exportar", "Exportar"),
    ]),
    ("purchases", "Compras", [("ver", "Ver"), ("crear", "Crear"), ("editar", "Editar")]),
    ("field", "Levantamientos y órdenes de trabajo", [
        ("ver", "Ver"), ("resumen", "Resumen en solo lectura"), ("ver_todo", "Ver los de todos"), ("crear", "Crear"), ("editar", "Editar"),
        ("asignar", "Asignar"), ("revisar", "Revisar y aprobar"),
    ]),
    ("projects", "Proyectos", [("ver", "Ver"), ("ver_todo", "Ver los de todos"), ("crear", "Crear"), ("editar", "Editar")]),
    ("assets", "Activos del cliente", [("ver", "Ver"), ("crear", "Crear"), ("editar", "Editar")]),
    ("support_desk", "Tickets de soporte", [("ver", "Ver"), ("ver_todo", "Ver los de todos"), ("crear", "Crear"), ("editar", "Atender")]),
    ("garantias", "Garantías", [("ver", "Ver"), ("crear", "Recibir"), ("editar", "Editar")]),
    ("events", "Agenda", [("ver", "Ver"), ("crear", "Crear"), ("editar", "Editar"), ("checkin", "Marcar llegada")]),
    ("commissions", "Comisiones", [("ver", "Ver las suyas"), ("ver_todo", "Ver las de todos"), ("aprobar", "Aprobar y pagar"), ("configurar", "Reglas")]),
    ("accounting", "Contabilidad", [("ver", "Ver"), ("crear", "Crear"), ("editar", "Editar"), ("exportar", "Exportar")]),
    ("payroll", "Planillas", [("ver", "Ver"), ("crear", "Crear"), ("editar", "Editar"), ("aprobar", "Aprobar"), ("configurar", "Configurar")]),
    ("vehicles", "Vehículos", [("ver", "Ver"), ("crear", "Crear"), ("editar", "Editar"), ("exportar", "Exportar")]),
    ("aliados", "Aliados", [("ver", "Ver"), ("mensualidad", "Mensualidad"), ("crear", "Crear"), ("editar", "Editar"), ("precios", "Precios"), ("usuarios", "Usuarios")]),
    ("approvals", "Aprobaciones", [("ver", "Ver la bandeja")]),
    ("portal_clientes", "Accesos de clientes", [("ver", "Ver solicitudes"), ("aprobar", "Aprobar accesos")]),
    ("reports", "Reportes", [("ver", "Ver"), ("exportar", "Exportar")]),
    ("settings", "Ajustes", [("ver", "Ver")]),
]

# Solo el administrador: ningun rol editado o creado los recibe (ver el docstring)
LOCKED: frozenset[str] = frozenset(
    {
        "sales.anular",
        "catalog.publicar",
        "catalog.costos",
        "sales.asignar",
        "sales.descuento_libre",
        "sales.aprobar",
        "field.saltar_revision",
        "settings.configurar",
    }
)

GRANTABLE: dict[str, tuple[str, ...]] = {m: tuple(a for a, _ in acts) for m, _, acts in CATALOG}


def problems(perms: dict) -> list[str]:
    """Permisos que un rol editado no puede tener: los de solo administrador, "*" y lo que no esta en el catalogo."""
    out: list[str] = []
    if not isinstance(perms, dict):
        return ["Formato inválido"]
    for mod, acts in perms.items():
        if mod == "*":
            out.append("*")
            continue
        for a in acts if isinstance(acts, list) else [acts]:
            pair = f"{mod}.{a}"
            if a == "*" or pair in LOCKED or a not in GRANTABLE.get(mod, ()):
                out.append(pair)
    return out


def clean(perms: dict | None) -> dict[str, list[str]]:
    """Lo que de verdad aplica de un mapa guardado: solo lo del catalogo y nunca lo de solo administrador (aunque alguien
    editara la base a mano). Orden estable (el del catalogo) y sin modulos vacios."""
    out: dict[str, list[str]] = {}
    if not isinstance(perms, dict):
        return out
    for mod, acts in GRANTABLE.items():
        tiene = perms.get(mod)
        if not isinstance(tiene, list):
            continue
        sel = [a for a in acts if a in tiene and f"{mod}.{a}" not in LOCKED]
        if sel:
            out[mod] = sel
    return out


def expand(perms: dict) -> dict[str, list[str]]:
    """Base para copiar un rol: "*" (administrador) pasa a todo lo que se puede repartir; el resto se limpia."""
    if "*" in perms:
        return clean({m: list(a) for m, a in GRANTABLE.items()})
    return clean(perms)


# Lo que ningun rol fuera del admin tiene NUNCA, ni una fila vieja de la tabla role cargada a mano (pedido 18 y tienda)
NUNCA: frozenset[str] = frozenset({"sales.anular", "catalog.publicar"})


def sin_anular_ni_publicar(perms: dict) -> dict:
    """Copia de un mapa de permisos sin "*" y sin anular ni publicar (roles personalizados viejos, fuera del catalogo)."""
    if not isinstance(perms, dict):
        return {}
    out: dict[str, list[str]] = {}
    for mod, acts in perms.items():
        if mod == "*" or not isinstance(acts, list):
            continue
        sel = [a for a in acts if a != "*" and f"{mod}.{a}" not in NUNCA]
        if sel:
            out[mod] = sel
    return out


# Permisos que hacen "sensible" a un rol: darlo (o agregarselo a un rol) pide la contrasena de quien lo hace, como dar el
# de administrador. (modulo, accion); None = cualquier accion del modulo.
SENSITIVE: tuple[tuple[str, str | None], ...] = (
    ("settings", None),
    ("accounting", None),
    ("payroll", None),
    ("aliados", "usuarios"),
    ("portal_clientes", "aprobar"),
)


def sensitive_hits(perms: dict | None) -> set[str]:
    """Que permisos sensibles trae un mapa ("accounting", "aliados.usuarios"...). "*" los trae todos."""
    if not isinstance(perms, dict):
        return set()
    if "*" in perms:
        return {mod if act is None else f"{mod}.{act}" for mod, act in SENSITIVE}
    out: set[str] = set()
    for mod, act in SENSITIVE:
        acts = perms.get(mod) or []
        if (act is None and acts) or (act is not None and (act in acts or "*" in acts)):
            out.add(mod if act is None else f"{mod}.{act}")
    return out
