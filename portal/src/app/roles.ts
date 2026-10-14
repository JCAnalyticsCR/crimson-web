/* Roles: nombre visible, color, que puede hacer y a donde lo lleva el inicio.
   La API es la que manda (cada endpoint exige modulo.accion); el portal solo esconde lo que el rol no puede usar. */

export type Perms = Record<string, string[]>;

export const can = (perms: Perms | undefined, module: string, action = "ver") => {
  if (!perms) return false;
  if (perms["*"]) return true;
  const acts = perms[module] || [];
  return acts.includes("*") || acts.includes(action);
};

/** "modulo.accion" o varias separadas por "|" (basta una). */
export const canAny = (perms: Perms | undefined, need?: string) =>
  !need || need.split("|").some((n) => { const [m, a] = n.split("."); return can(perms, m, a || "ver"); });

/* `menu` (opcional): las secciones del menú lateral que ve ese rol (rutas de NAV, app/nav.ts). Se cruza con los
   permisos: una sección listada sin permiso no aparece. Sin `menu` (admin, roles personalizados, caja, bodega,
   lectura, soporte) el menú sale solo de los permisos. Andrés 23:18-24:48: "definir bien qué ve cada persona… lo
   único que tiene que mostrar". El menú NO es seguridad: la API manda. Una sección nueva para un rol con lista se
   suma aquí a su `menu` (p. ej. "/aprobaciones" ya está en el del supervisor). */
export type RoleMeta = { label: string; tone: string; glyph: string; summary: string; can: string[]; cannot: string[]; menu?: string[] };

/* El orden es el de Ajustes y "Ver como" (el mismo que ROLE_PERMISSIONS en la API): primero los que Andrés pidió
   pulir (32:05): administrador, vendedor, contador, técnico instalador y aliados. */
export const ROLES: Record<string, RoleMeta> = {
  admin: {
    label: "Administrador", tone: "#e2233a", glyph: "A",
    summary: "Control total de la empresa. Es el único que ve costos, márgenes y utilidad (salvo que habilite otro rol en Ajustes).",
    can: ["Ver todos los números de la empresa, incluido el costo", "Aprobaciones: todo lo que espera tu visto bueno en un solo lugar", "Costear levantamientos y aprobar cotizaciones", "Configurar facturación, usuarios y pasarelas", "Planillas, conciliación y reportes fiscales"],
    cannot: [],
  },
  ventas: {
    label: "Vendedor", tone: "#2f6fed", glyph: "V",
    summary: "Cotiza y factura con el precio de venta. Ve solo sus oportunidades, cotizaciones, facturas y comisiones; nunca el costo.",
    can: ["Sus oportunidades y cotizaciones (el consecutivo es el de la empresa)", "Facturar y buscar sus facturas", "Ver en resumen los levantamientos de sus oportunidades y pedir uno nuevo", "Productos y servicios con el precio de venta", "Pedidos de la tienda", "Sus comisiones en tiempo real"],
    cannot: ["Ver el costo del proveedor, el margen o la utilidad", "Ver lo que venden los demás o el total de la empresa", "Llenar o editar levantamientos", "Descontar más del límite sin aprobación", "Anular facturas"],
    menu: ["/", "/oportunidades", "/cotizaciones", "/facturas", "/pagos", "/levantamientos", "/clientes", "/productos", "/ordenes", "/comisiones", "/archivo", "/ajustes"],
  },
  contabilidad: {
    label: "Contador", tone: "#6b46c1", glyph: "K",
    summary: "Todo el dinero de la empresa: facturas, cobros, gastos, recepción de XML, conciliación, planillas y reportes.",
    can: ["Facturas y cobros de toda la empresa", "Gastos, recepción y conciliación", "Planillas, colillas y comisiones", "Aprobaciones de planillas y comisiones", "Los reportes de la empresa con Excel"],
    cannot: ["Crear ventas o cobrar", "Ver costos de productos ni márgenes (salvo que el administrador lo habilite)", "Configurar la empresa o los usuarios"],
    menu: ["/", "/aprobaciones", "/facturas", "/pagos", "/recurrencias", "/clientes", "/productos", "/inventario", "/compras", "/comisiones", "/contabilidad", "/recepcion", "/conciliacion", "/planillas", "/vehiculos", "/reportes", "/archivo", "/ajustes"],
  },
  tecnico: {
    label: "Técnico instalador", tone: "#b45309", glyph: "T",
    summary: "Solo lo suyo, desde el celular: el trabajo del día, el levantamiento en sitio y la evidencia de la instalación.",
    can: ["Ver sus órdenes de trabajo del día", "Llenar levantamientos técnicos en sitio", "Atender los tickets de soporte que le asignan", "Registrar material usado, horas, fotos y firma del cliente", "Registrar su tiempo por jornada en la orden", "Anotar los equipos que deja instalados"],
    cannot: ["Ver precios, costos ni montos", "Ver trabajos de otros técnicos", "Ver facturas, cobros o reportes"],
    menu: ["/", "/ordenes-trabajo", "/levantamientos", "/tickets", "/mantenimientos", "/activos", "/inventario", "/ajustes"],
  },
  aliado: {
    label: "Aliado comercial", tone: "#4338ca", glyph: "AL",
    summary: "Empresa aliada: ve solo lo que tiene en común con Crimson — oportunidades, levantamientos, proyectos, tickets y mantenimientos — y el catálogo con su precio de aliado.",
    can: ["Oportunidades y proyectos en común", "Hacer levantamientos de lo que llevan en común", "Darle sus precios a Crimson cuando se los pide", "Tickets y mantenimientos asignados a su empresa", "Catálogo con precio de aliado e inventario (solo cantidades)", "Sus ganancias por proyecto"],
    cannot: ["Cotizar o facturar a nombre de Crimson", "Ver clientes, proyectos o tickets que no sean en común", "Ver costos de Crimson ni lo que Crimson le cobra al cliente", "Ver contabilidad, planillas o reportes"],
    menu: ["/", "/oportunidades", "/proyectos", "/ordenes-trabajo", "/levantamientos", "/tickets", "/mantenimientos", "/activos", "/clientes", "/productos", "/inventario", "/comisiones", "/ajustes"],
  },
  tecnico_aliado: {
    label: "Técnico de aliado", tone: "#9a3412", glyph: "TA",
    summary: "Técnico de una empresa aliada: solo lo que le toca hoy — sus levantamientos, órdenes de trabajo, tickets y mantenimientos asignados.",
    can: ["Ver su trabajo del día", "Llenar levantamientos en sitio", "Atender los tickets y mantenimientos que le asignan", "Registrar material, horas, fotos y firma"],
    cannot: ["Ver precios ni costos", "Ver activos o datos de clientes fuera de su trabajo", "Ver trabajo de otros técnicos"],
    menu: ["/", "/ordenes-trabajo", "/levantamientos", "/tickets", "/mantenimientos", "/ajustes"],
  },
  supervisor: {
    label: "Supervisor técnico", tone: "#0f766e", glyph: "S",
    summary: "Manda el campo: revisa levantamientos, asigna órdenes de trabajo, proyectos y materiales. El costeo es del administrador.",
    can: ["Asignar y programar órdenes de trabajo", "Revisar y aprobar levantamientos antes del costeo", "Bandeja de Aprobaciones: levantamientos por revisar y reservas de material", "Proyectos, activos del cliente y solicitudes de compra", "Toda la mesa de soporte y los mantenimientos", "Movimientos de inventario"],
    cannot: ["Ver costos o márgenes (salvo que el administrador lo habilite)", "Facturar o cobrar", "Ver planillas, gastos ni conciliación", "Configurar la empresa"],
    menu: ["/", "/oportunidades", "/proyectos", "/ordenes-trabajo", "/levantamientos", "/aprobaciones", "/tickets", "/mantenimientos", "/activos", "/clientes", "/accesos-clientes", "/productos", "/inventario", "/compras", "/vehiculos", "/reportes", "/archivo", "/ajustes"],
  },
  caja: {
    label: "Caja", tone: "#0e9f6e", glyph: "C",
    summary: "Cobra en mostrador y registra pagos de cualquier factura. Ve el cierre de caja, no la contabilidad.",
    can: ["Punto de venta y cobros de cualquier factura", "Cierre de caja, transacciones y propinas"],
    cannot: ["Ver costos, gastos, planillas o resultados", "Anular documentos", "Configurar la empresa"],
  },
  inventario: {
    label: "Bodega", tone: "#b7791f", glyph: "B",
    summary: "Catálogo y existencias por ubicación. No ve ventas, clientes, costos ni dinero.",
    can: ["Productos, variantes e imágenes (con precio de venta)", "Entradas, salidas y transferencias", "Reportes de inventario y movimientos"],
    cannot: ["Ver el costo del proveedor o importar listas de proveedor (salvo que el administrador lo habilite)", "Ver ventas, clientes o cobros", "Ver contabilidad o planillas"],
  },
  lectura: {
    label: "Solo lectura", tone: "#6b6570", glyph: "L",
    summary: "Para socios o auditores: ve ventas y cobros de la empresa sin modificar nada.",
    can: ["Ver facturas, cotizaciones y clientes", "Reportes de ventas y de caja"],
    cannot: ["Crear o modificar cualquier dato", "Ver costos, gastos o planillas", "Exportar a Excel"],
  },
  // Roles de cliente: no entran al panel interno, tienen su propio portal (modules/cliente)
  cliente_admin: {
    label: "Cliente · administrador", tone: "#0e7490", glyph: "C",
    summary: "Encargado de una empresa cliente: tickets de toda su empresa, equipos, mantenimientos, cotizaciones y facturas, y los usuarios de su empresa.",
    can: ["Reportar fallas y seguir los tickets de su empresa", "Ver sus equipos instalados y las próximas visitas", "Descargar sus cotizaciones y facturas", "Invitar o desactivar usuarios de su empresa"],
    cannot: ["Ver nada de otros clientes", "Ver costos, notas internas o la operación de Crimson"],
  },
  cliente_usuario: {
    label: "Cliente · usuario", tone: "#0891b2", glyph: "U",
    summary: "Persona de una empresa cliente: reporta fallas y sigue sus tickets; ve los equipos instalados.",
    can: ["Reportar fallas con fotos", "Seguir sus tickets y ver los de su empresa", "Ver los equipos instalados"],
    cannot: ["Ver cotizaciones o facturas", "Invitar usuarios", "Ver nada de otros clientes"],
  },
  soporte: { label: "Soporte temporal", tone: "#6b6570", glyph: "S", summary: "Acceso de solo lectura concedido por el administrador; cada pantalla queda en la bitácora.", can: ["Ver para diagnosticar"], cannot: ["Modificar cualquier dato"] },
};

export const isClientRole = (code: string | undefined) => code === "cliente_admin" || code === "cliente_usuario";
export const isAllyRole = (code: string | undefined) => code === "aliado" || code === "tecnico_aliado";

/** La sección (o una ruta dentro de ella, p. ej. /cotizaciones/nueva) está en el menú de ese rol. Sin lista: sí. */
export const inRoleMenu = (code: string | undefined, to: string) => {
  const menu = ROLES[code || ""]?.menu;
  if (!menu) return true;
  return menu.some((m) => (m === "/" ? to === "/" : to === m || to.startsWith(`${m}/`)));
};

export const roleMeta = (code: string | undefined): RoleMeta => ROLES[code || ""] ?? { label: code || "—", tone: "#6b6570", glyph: "?", summary: "", can: [], cannot: [] };
