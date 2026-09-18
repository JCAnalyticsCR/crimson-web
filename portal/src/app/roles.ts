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
    summary: "Cotiza, factura y cobra. Ve sus propias ventas y el catálogo, sin tocar la contabilidad.",
    can: ["Cotizar y convertir a factura", "Cobrar en el punto de venta y registrar pagos", "Gestionar clientes, órdenes y eventos", "Ver sus propias ventas del mes"],
    cannot: ["Anular facturas", "Ver contabilidad, planillas ni reportes de la empresa", "Cambiar precios del catálogo o la configuración"],
  },
  caja: {
    label: "Caja", tone: "#0e9f6e", glyph: "C",
    summary: "Cobra en mostrador y registra pagos. Cierre de caja diario.",
    can: ["Punto de venta y cobros", "Control de acceso de eventos", "Cierre de caja"], cannot: ["Configurar", "Contabilidad"],
  },
  inventario: {
    label: "Bodega", tone: "#b7791f", glyph: "B",
    summary: "Catálogo y existencias por ubicación.", can: ["Productos y variantes", "Movimientos y transferencias"], cannot: ["Ventas", "Contabilidad"],
  },
  contabilidad: {
    label: "Contabilidad", tone: "#6b46c1", glyph: "K",
    summary: "Gastos, recepción de XML, conciliación, planillas y reportes.", can: ["Gastos y recepción", "Conciliación y planillas", "Reportes y Excel"], cannot: ["Configurar la empresa"],
  },
  lectura: { label: "Solo lectura", tone: "#6b6570", glyph: "L", summary: "Consulta sin modificar nada.", can: ["Ver documentos y clientes"], cannot: ["Crear o modificar"] },
  soporte: { label: "Soporte temporal", tone: "#6b6570", glyph: "S", summary: "Acceso de solo lectura concedido por el administrador; cada pantalla queda en la bitácora.", can: ["Ver para diagnosticar"], cannot: ["Modificar cualquier dato"] },
};

export const roleMeta = (code: string | undefined): RoleMeta => ROLES[code || ""] ?? { label: code || "—", tone: "#6b6570", glyph: "?", summary: "", can: [], cannot: [] };
