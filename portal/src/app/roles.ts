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

export type RoleMeta = { label: string; tone: string; glyph: string; summary: string; can: string[]; cannot: string[] };

export const ROLES: Record<string, RoleMeta> = {
  admin: {
    label: "Administrador", tone: "#e2233a", glyph: "A",
    summary: "Control total de la empresa: ventas, cobros, contabilidad, planillas, tienda y ajustes.",
    can: ["Ver todos los números de la empresa", "Configurar facturación, usuarios y pasarelas", "Anular documentos y emitir notas de crédito", "Planillas, conciliación y reportes fiscales"],
    cannot: [],
  },
  ventas: {
    label: "Vendedor", tone: "#2f6fed", glyph: "V",
    summary: "Cotiza y factura con el precio de venta. Ve solo sus propios documentos y nunca el costo del proveedor; el dinero de la empresa lo ven caja y contabilidad.",
    can: ["Cotizar y convertir a factura", "Vender en el punto de venta", "Gestionar clientes, órdenes y eventos", "Abrir tickets de soporte de sus clientes", "Ver su facturación del mes, sus cobros y sus comisiones"],
    cannot: ["Ver el costo del proveedor, el margen o la utilidad", "Descontar más del límite sin aprobación de un administrador", "Anular facturas", "Ver cobros, contabilidad, planillas o reportes de la empresa"],
  },
  caja: {
    label: "Caja", tone: "#0e9f6e", glyph: "C",
    summary: "Cobra en mostrador y registra pagos de cualquier factura. Ve el cierre de caja, no la contabilidad.",
    can: ["Punto de venta y cobros de cualquier factura", "Cierre de caja, transacciones y propinas", "Control de acceso de eventos"],
    cannot: ["Ver gastos, planillas o resultados", "Anular documentos", "Configurar la empresa"],
  },
  inventario: {
    label: "Bodega", tone: "#b7791f", glyph: "B",
    summary: "Catálogo y existencias por ubicación. No ve ventas, clientes ni dinero.",
    can: ["Productos, variantes e imágenes", "Entradas, salidas y transferencias", "Reportes de inventario y movimientos"],
    cannot: ["Ver ventas, clientes o cobros", "Ver contabilidad o planillas"],
  },
  contabilidad: {
    label: "Contabilidad", tone: "#6b46c1", glyph: "K",
    summary: "Todo el dinero de la empresa: gastos, recepción de XML, conciliación, planillas y reportes.",
    can: ["Gastos, recepción y conciliación", "Planillas, colillas y comisiones", "Los reportes de la empresa con Excel, incluida la rentabilidad por proyecto"],
    cannot: ["Crear ventas o cobrar", "Configurar la empresa o los usuarios"],
  },
  supervisor: {
    label: "Supervisor técnico", tone: "#0f766e", glyph: "S",
    summary: "Manda el campo: levantamientos, proyectos, órdenes de trabajo y materiales. Ve costos, no la contabilidad.",
    can: ["Asignar y programar órdenes de trabajo", "Revisar levantamientos y costear materiales", "Proyectos, activos del cliente y solicitudes de compra", "Toda la mesa de soporte y los mantenimientos", "Movimientos de inventario"],
    cannot: ["Facturar o cobrar", "Ver planillas, gastos ni conciliación", "Configurar la empresa"],
  },
  tecnico: {
    label: "Técnico instalador", tone: "#b45309", glyph: "T",
    summary: "Solo lo suyo, desde el celular: el trabajo del día, el levantamiento en sitio y la evidencia de la instalación.",
    can: ["Ver sus órdenes de trabajo del día", "Llenar levantamientos técnicos en sitio", "Atender los tickets de soporte que le asignan", "Registrar material usado, fotos y firma del cliente", "Anotar los equipos que deja instalados"],
    cannot: ["Ver precios, costos ni márgenes", "Ver trabajos de otros técnicos", "Ver facturas, cobros o reportes"],
  },
  lectura: {
    label: "Solo lectura", tone: "#6b6570", glyph: "L",
    summary: "Para socios o auditores: ve ventas y cobros de la empresa sin modificar nada.",
    can: ["Ver facturas, cotizaciones y clientes", "Reportes de ventas y de caja"],
    cannot: ["Crear o modificar cualquier dato", "Ver gastos o planillas", "Exportar a Excel"],
  },
  soporte: { label: "Soporte temporal", tone: "#6b6570", glyph: "S", summary: "Acceso de solo lectura concedido por el administrador; cada pantalla queda en la bitácora.", can: ["Ver para diagnosticar"], cannot: ["Modificar cualquier dato"] },
};

export const roleMeta = (code: string | undefined): RoleMeta => ROLES[code || ""] ?? { label: code || "—", tone: "#6b6570", glyph: "?", summary: "", can: [], cannot: [] };
