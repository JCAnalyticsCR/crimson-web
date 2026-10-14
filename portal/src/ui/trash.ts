/* Que se puede mandar a la papelera, con que permiso y donde se abre. Las reglas de verdad (dependencias, motivos)
   viven en la API (services/archive.py: KINDS, ACTION, NEEDS_ALL); aqui solo lo necesario para dibujar el icono y
   los enlaces. */

export type TrashKind =
  | "survey" | "project" | "opportunity" | "work_order"
  | "ticket" | "contract" | "asset" | "purchase" | "customer" | "product" | "supplier" | "coupon";

type Cfg = { module: string; label: string; link: (id: number) => string; action?: string; needsAll?: boolean };

export const TRASH: Record<TrashKind, Cfg> = {
  survey: { module: "field", label: "Levantamientos", link: (id) => `/levantamientos?id=${id}` },
  project: { module: "projects", label: "Proyectos", link: (id) => `/proyectos/${id}` },
  opportunity: { module: "crm_pipeline", label: "Oportunidades", link: (id) => `/oportunidades?id=${id}` },
  work_order: { module: "field", label: "Órdenes de trabajo", link: (id) => `/ordenes-trabajo?id=${id}` },
  ticket: { module: "support_desk", label: "Tickets de soporte", link: (id) => `/tickets?id=${id}`, needsAll: true },
  contract: { module: "support_desk", label: "Contratos de mantenimiento", link: () => "/mantenimientos", needsAll: true },
  asset: { module: "assets", label: "Activos de clientes", link: () => "/activos" },
  purchase: { module: "purchases", label: "Solicitudes de compra", link: () => "/compras", action: "crear" },
  customer: { module: "crm", label: "Clientes", link: (id) => `/clientes/${id}` },
  product: { module: "catalog", label: "Productos y servicios", link: () => "/productos" },
  supplier: { module: "catalog", label: "Proveedores", link: () => "/contabilidad" },
  coupon: { module: "catalog", label: "Cupones", link: () => "/cupones" },
};

/** Permiso para mandar a la papelera, archivar y restaurar (la API vuelve a verificarlo). */
export const trashNeed = (kind: TrashKind) => {
  const c = TRASH[kind];
  const need = `${c.module}.${c.action || "editar"}`;
  return c.needsAll ? [need, `${c.module}.ver_todo`] : [need];
};

/** Para lo que nunca se borra: que hacer en su lugar (el icono se ve apagado y lo explica). */
export const LOCKED = {
  invoice: "Las facturas son comprobantes electrónicos: no se borran. Si hubo un error, anulala o emití una nota de crédito.",
  quote: "Las cotizaciones no se borran: anulala desde la cotización. Así queda la constancia de lo que se envió.",
  payment: "Los pagos no se borran: quedan ligados a su factura y al cierre de caja. Si hubo un error, anulá la factura con nota de crédito.",
  expense: "Los gastos no se borran: abrilo y cambiá su estado a «anulado»; queda la constancia.",
  fuel: "Las cargas de combustible no se borran: editala y marcala como excluida (con el motivo); deja de contar.",
  order: "Los pedidos web no se borran: abrilo y cancelalo o reembolsalo.",
} as const;
