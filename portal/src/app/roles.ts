/* Roles: nombre visible, color, que puede hacer y a donde lo lleva el inicio.
   La API es la que manda (cada endpoint exige modulo.accion); el portal solo esconde lo que el rol no puede usar. */
import { NAV } from "./nav";

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
/* `top` (opcional): secciones que suben al principio del menú, justo debajo de Inicio (lo que ese rol usa a diario).
   `tabs` (opcional): barra inferior en el celular con esas secciones y un botón "Más" que abre el menú completo. */
export type RoleMeta = { label: string; tone: string; glyph: string; summary: string; can: string[]; cannot: string[]; menu?: string[]; top?: string[]; tabs?: string[] };

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
    can: ["Cobrar en el punto de venta y crear ahí el cliente, que queda en su cartera", "Sus oportunidades y cotizaciones (el consecutivo es el de la empresa)", "Facturar y buscar sus facturas", "Ver en resumen los levantamientos de sus oportunidades y pedir uno nuevo", "Productos y servicios con el precio de venta", "Existencias y ajustes de inventario (entradas, salidas y ajustes)", "Pedidos de la tienda", "Ver en tiempo real cuánto lleva ganado en comisiones"],
    cannot: ["Ver el costo del proveedor, el margen o la utilidad", "Ver lo que venden los demás o el total de la empresa", "Llenar o editar levantamientos", "Descontar más del límite sin aprobación", "Anular facturas", "Marcar facturas emitidas en Fygaro (lo hace contabilidad)"],
    menu: ["/", "/oportunidades", "/cotizaciones", "/facturas", "/pagos", "/pos", "/levantamientos", "/clientes", "/productos", "/inventario", "/ordenes", "/comisiones", "/archivo", "/ajustes"],
    // 26/10: vende desde el celular. Lo de todos los días arriba y en la barra inferior: vender, sus comisiones y sus clientes
    top: ["/pos", "/comisiones"],
    tabs: ["/", "/pos", "/cotizaciones", "/comisiones"],
  },
  contabilidad: {
    label: "Contador", tone: "#6b46c1", glyph: "K",
    summary: "Todo el dinero de la empresa: facturas de proveedores ante Hacienda, gastos, bancos, el panel tributario, planillas, comisiones y reportes.",
    can: ["Facturas de proveedores: recibir, aceptar o rechazar ante Hacienda", "Gastos, recurrentes, categorías y bancos", "Panel tributario del mes", "Facturas y cobros de toda la empresa", "Enviar facturas, crear enlaces de pago y registrar cobros", "Emitir a Hacienda o marcar lo emitido en Fygaro (lista «Por emitir»)", "Planillas, colillas y comisiones", "Aprobaciones de planillas y comisiones", "Aprobar y marcar pagadas las comisiones y los pagos a aliados", "La mensualidad de las empresas aliadas", "Los reportes de la empresa con Excel", "Importar el histórico de recepciones de Fygaro"],
    cannot: ["Crear o editar ventas (cotizaciones y facturas) ni enviar cotizaciones", "Ver costos de productos ni márgenes (salvo que el administrador lo habilite)", "Configurar la empresa o los usuarios", "Cargar las credenciales de Hacienda ni activar producción"],
    menu: ["/", "/aprobaciones", "/facturas", "/pagos", "/recurrencias", "/clientes", "/productos", "/inventario", "/compras", "/comisiones", "/aliados", "/contabilidad", "/planillas", "/vehiculos", "/reportes", "/importar", "/archivo", "/ajustes"],
  },
  tecnico: {
    label: "Técnico instalador", tone: "#b45309", glyph: "T",
    summary: "Solo lo suyo, desde el celular: el trabajo del día, el levantamiento en sitio y la evidencia de la instalación.",
    can: ["Ver sus órdenes de trabajo del día", "Llenar levantamientos técnicos en sitio", "Atender los tickets de soporte que le asignan", "Registrar material usado, horas, fotos y firma del cliente", "Registrar su tiempo por jornada en la orden", "Anotar los equipos que deja instalados", "Recibir equipos en garantía y hacer la revisión de Crimson"],
    cannot: ["Ver precios, costos ni montos", "Ver trabajos de otros técnicos", "Ver facturas, cobros o reportes"],
    menu: ["/", "/ordenes-trabajo", "/levantamientos", "/tickets", "/garantias", "/mantenimientos", "/activos", "/inventario", "/ajustes"],
  },
  // Roles de empresa aliada: no entran al panel interno (sin `menu`): tienen su propio portal (modules/aliado, AllyShell)
  aliado: {
    label: "Aliado comercial", tone: "#4338ca", glyph: "AL",
    summary: "Empresa aliada: ve solo lo que tiene en común con Crimson — oportunidades, levantamientos, proyectos, tickets y mantenimientos — y el catálogo con su precio de aliado.",
    can: ["Oportunidades y proyectos en común", "Hacer levantamientos de lo que llevan en común", "Darle sus precios a Crimson cuando se los pide", "Tickets y mantenimientos asignados a su empresa", "Catálogo con precio de aliado e inventario (solo cantidades)", "Ver sus ganancias por proyecto y su rentabilidad en el tiempo"],
    cannot: ["Cotizar o facturar a nombre de Crimson", "Ver clientes, proyectos o tickets que no sean en común", "Ver costos de Crimson ni lo que Crimson le cobra al cliente", "Ver contabilidad, planillas o reportes"],
  },
  tecnico_aliado: {
    label: "Técnico de aliado", tone: "#9a3412", glyph: "TA",
    summary: "Técnico de una empresa aliada: solo lo que le toca hoy — sus levantamientos, órdenes de trabajo, tickets y mantenimientos asignados.",
    can: ["Ver su trabajo del día", "Llenar levantamientos en sitio", "Atender los tickets y mantenimientos que le asignan", "Registrar material, horas, fotos y firma"],
    cannot: ["Ver precios ni costos", "Ver activos o datos de clientes fuera de su trabajo", "Ver trabajo de otros técnicos"],
  },
  supervisor: {
    label: "Supervisor técnico", tone: "#0f766e", glyph: "S",
    summary: "Manda el campo: revisa levantamientos, asigna órdenes de trabajo, proyectos y materiales. El costeo es del administrador.",
    can: ["Asignar y programar órdenes de trabajo", "Revisar y aprobar levantamientos antes del costeo", "Bandeja de Aprobaciones: levantamientos por revisar y reservas de material", "Proyectos, activos del cliente y solicitudes de compra", "Toda la mesa de soporte y los mantenimientos", "Movimientos de inventario"],
    cannot: ["Ver costos o márgenes (salvo que el administrador lo habilite)", "Facturar o cobrar", "Ver planillas, gastos ni conciliación", "Configurar la empresa"],
    menu: ["/", "/oportunidades", "/proyectos", "/ordenes-trabajo", "/levantamientos", "/aprobaciones", "/tickets", "/garantias", "/mantenimientos", "/activos", "/clientes", "/accesos-clientes", "/productos", "/inventario", "/compras", "/vehiculos", "/reportes", "/archivo", "/ajustes"],
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
    can: ["Productos, variantes e imágenes (con precio de venta)", "Transferencias entre ubicaciones y series", "Reportes de inventario y movimientos"],
    cannot: ["Ver el costo del proveedor o importar listas de proveedor (salvo que el administrador lo habilite)", "Hacer ajustes de inventario (solo vendedores y administrador)", "Ver ventas, clientes o cobros", "Ver contabilidad o planillas"],
  },
  lectura: {
    label: "Solo lectura", tone: "#6b6570", glyph: "L",
    summary: "Para socios o auditores: ve ventas y cobros de la empresa sin modificar nada.",
    can: ["Ver facturas, cotizaciones y clientes", "Reportes de ventas y de caja"],
    cannot: ["Crear o modificar cualquier dato", "Ver costos, gastos o planillas", "Exportar a Excel"],
  },
  /* ventas etapa 2: no se edita en Roles (aprueba hasta sus topes y maneja la cartera); sus topes y si asigna ventas
     se configuran en Ajustes → Ventas */
  jefe_ventas: {
    label: "Jefe de ventas", tone: "#1d4ed8", glyph: "JV",
    summary: "Dirige al equipo de ventas: ve las oportunidades, cotizaciones, facturas y cobros de todos, aprueba descuentos y cotizaciones grandes hasta sus topes y reparte la cartera. Nunca ve el costo ni el margen.",
    can: ["Ver lo que vende todo el equipo y el embudo del equipo", "Aprobar descuentos y montos hasta sus topes (Ajustes → Ventas)", "Cambiar el vendedor de la cuenta de un cliente", "Traspasar la cartera de un vendedor a otro", "Asignar ventas por asignar, si el administrador lo habilita"],
    cannot: ["Ver el costo, el margen o la utilidad", "Aprobar lo que pase de sus topes, el margen bajo el mínimo o sus propias cotizaciones", "Anular facturas", "Configurar la empresa o los usuarios"],
    menu: ["/", "/aprobaciones", "/oportunidades", "/cotizaciones", "/facturas", "/pagos", "/metas", "/clientes", "/productos", "/inventario", "/ordenes", "/comisiones", "/archivo", "/ajustes"],
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
/** Cliente o aliado: tienen su propio portal y nunca el panel interno. */
export const isExternalRole = (code: string | undefined) => isClientRole(code) || isAllyRole(code);

/* Ajustes → Usuarios → Roles (reunión 21/10, pedido 20): roles que creó el administrador (con su nombre) y roles cuyos
   permisos cambió. La API los manda (/auth/me, /auth/roles, /settings/users) y aquí se registran. Un rol cambiado
   CONSERVA el `menu` fijo de su rol (o, si lo creó el admin, el del rol del que salió) y solo suma las secciones de lo
   que el admin le agregó respecto de los permisos de fábrica: quitarle algo nunca destapa lo que el menú ocultaba a
   propósito (p. ej. el técnico tiene crm.ver solo para los buscadores y Clientes no sale en su menú). */
type RoleInfo = { base?: string | null; defaults?: Perms | null; perms?: Perms | null };
const CUSTOM_LABELS: Record<string, string> = {};
const CUSTOMIZED = new Set<string>();
const INFO: Record<string, RoleInfo> = {};

/** replace: lo que llega es la lista completa (p. ej. /auth/roles): se olvida lo que ya no viene (un rol borrado o
    restablecido de fábrica no sigue en «Ver como» ni con el menú armado de antes). */
export function registerRoles(info: {
  labels?: Record<string, string> | null; customized?: string[] | null; replace?: boolean;
  bases?: Record<string, string | null> | null; defaults?: Record<string, Perms> | null; perms?: Record<string, Perms> | null;
}) {
  if (info.labels) {
    if (info.replace) Object.keys(CUSTOM_LABELS).forEach((k) => { delete CUSTOM_LABELS[k]; });
    Object.assign(CUSTOM_LABELS, info.labels);
  }
  if (info.customized) {
    if (info.replace) CUSTOMIZED.clear();
    info.customized.forEach((c) => CUSTOMIZED.add(c));
  }
  Object.entries(info.bases || {}).forEach(([c, b]) => { INFO[c] = { ...INFO[c], base: b }; });
  Object.entries(info.defaults || {}).forEach(([c, d]) => { INFO[c] = { ...INFO[c], defaults: d }; });
  Object.entries(info.perms || {}).forEach(([c, x]) => { INFO[c] = { ...INFO[c], perms: x }; });
}

/** El rol propio según /auth/me: se pone o se saca (restablecido de fábrica) sin esperar a recargar la página. */
export function registerOwnRole(code: string, r: { label?: string | null; customized?: boolean; base?: string | null; defaults?: Perms | null; perms?: Perms | null }) {
  if (!code) return;
  if (r.label) CUSTOM_LABELS[code] = r.label;
  if (r.customized) CUSTOMIZED.add(code); else CUSTOMIZED.delete(code);
  INFO[code] = { ...INFO[code], base: r.base ?? null, defaults: r.defaults ?? null, perms: r.perms ?? null };
}

/** Códigos de los roles creados por el administrador que ya se conocen (en el orden en que llegaron). */
export const customRoleCodes = () => Object.keys(CUSTOM_LABELS).filter((c) => !ROLES[c]);

const inList = (menu: string[], to: string) => menu.some((m) => (m === "/" ? to === "/" : to === m || to.startsWith(`${m}/`)));
const NAV_ITEMS = NAV.flatMap((g) => g.items);
const navNeed = (to: string) => {
  const it = NAV_ITEMS.filter((x) => (x.to === "/" ? to === "/" : to === x.to || to.startsWith(`${x.to}/`))).sort((a, b) => b.to.length - a.to.length)[0];
  return it ? it.need : undefined;
};
/** Lo que tiene de más respecto de los permisos de fábrica de su rol. */
const added = (perms: Perms, defaults: Perms): Perms => {
  const out: Perms = {};
  for (const [m, acts] of Object.entries(perms)) {
    const extra = acts.filter((a) => !(defaults[m] || []).includes(a));
    if (extra.length) out[m] = extra;
  }
  return out;
};
/* el menú fijo de un rol: el suyo o, si lo creó el admin, el del rol del que salió */
const menuOf = (code: string) => {
  if (ROLES[code]) return ROLES[code].menu;
  const base = INFO[code]?.base;
  return base && ROLES[base] ? ROLES[base].menu : undefined;
};

/** La sección (o una ruta dentro de ella, p. ej. /cotizaciones/nueva) está en el menú de ese rol. Sin lista: sí. */
export const inRoleMenu = (code: string | undefined, to: string) => {
  const c = code || "";
  const menu = menuOf(c);
  if (!menu || inList(menu, to)) return true;
  if (ROLES[c] && !CUSTOMIZED.has(c)) return false;
  const info = INFO[c];
  if (!info?.perms) return false;
  const need = navNeed(to);
  return !!need && canAny(added(info.perms, info.defaults || {}), need);
};

export const roleMeta = (code: string | undefined): RoleMeta => {
  const base = ROLES[code || ""];
  if (base) return base;
  const label = CUSTOM_LABELS[code || ""];
  return label
    ? { label, tone: "#7c5c3b", glyph: label.charAt(0).toUpperCase(), summary: "Rol creado por el administrador: puede lo que se marcó en Ajustes → Usuarios → Roles.", can: [], cannot: [] }
    : { label: code || "—", tone: "#6b6570", glyph: "?", summary: "", can: [], cannot: [] };
};
