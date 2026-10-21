/* Cliente HTTP: token de acceso en memoria, refresh automatico por cookie httpOnly, errores tipados. */

export class ApiError extends Error {
  /** data: el cuerpo de la respuesta de error (p. ej. {detail, existing} de un 409 por duplicado). */
  constructor(public status: number, message: string, public headers?: Headers, public data?: unknown) {
    super(message);
  }
}

/* "Ver como este aliado" (solo admin): el portal de aliados se dibuja con los datos REALES de esa empresa. La API lo
   aplica solo a /aliado/* y solo para lectura (core/ally.py); el navegador manda la empresa elegida en cabeceras. */
export const ALLY_PREVIEW_KEY = "crimson-view-ally";
export type AllyPreview = {
  company_id: number; user_id?: number | null; role: "aliado" | "tecnico_aliado"; name?: string; user_name?: string | null;
  /** como estaba la empresa al activar la vista previa (POST /allies/{id}/preview): la franja de suspendida y el menú */
  state?: "activo" | "suspendido" | "inactivo"; suspended_reason?: string | null; features?: { ganancias: boolean };
};
export function readAllyPreview(): AllyPreview | null {
  try { const raw = sessionStorage.getItem(ALLY_PREVIEW_KEY); return raw ? (JSON.parse(raw) as AllyPreview) : null; } catch { return null; }
}
function previewHeaders(path: string): Record<string, string> {
  if (!path.startsWith("/aliado")) return {};
  const v = readAllyPreview();
  if (!v?.company_id) return {};
  const h: Record<string, string> = { "X-View-As-Ally": String(v.company_id) };
  if (v.user_id) h["X-View-As-User"] = String(v.user_id);
  return h;
}

/* 2FA obligatorio para el rol con la gracia vencida (plan 1.10): la API responde 403 con X-2FA-Setup y la sesion
   recarga /auth/me para mostrar la pantalla de configuracion forzada (app/session.tsx). */
export const TWOFA_EVENT = "crimson:2fa-setup";

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
const listeners = new Set<() => void>();

export const auth = {
  get token() { return accessToken; },
  set(token: string | null) { accessToken = token; listeners.forEach((l) => l()); },
  onChange(l: () => void) { listeners.add(l); return () => listeners.delete(l); },
};

async function tryRefresh(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch("/api/auth/refresh", { method: "POST", credentials: "include" })
      .then(async (r) => { if (!r.ok) return false; const j = await r.json(); auth.set(j.access_token); return true; })
      .catch(() => false)
      .finally(() => { refreshing = null; });
  }
  return refreshing;
}

/* ---------- Descargas y vistas con sesion ----------
   Los enlaces <a href="/api/..."> no llevan el token (vive en memoria), por eso se piden con fetch + Bearer
   y se entregan como blob: descarga (Excel, XML) o pestana nueva (PDF/HTML imprimible). */
async function fetchBlob(path: string, retry = true): Promise<Blob> {
  const headers: Record<string, string> = { ...previewHeaders(path) };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const r = await fetch(`/api${path}`, { headers, credentials: "include" });
  if (r.status === 401 && retry && (await tryRefresh())) return fetchBlob(path, false);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : msg; } catch { /* sin cuerpo */ }
    throw new ApiError(r.status, msg);
  }
  return r.blob();
}

export async function downloadFile(path: string, filename: string) {
  const blob = await fetchBlob(path);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

export async function openFile(path: string) {
  const win = window.open("about:blank", "_blank"); // abrir antes del await evita el bloqueador de ventanas
  try {
    const blob = await fetchBlob(path);
    const url = URL.createObjectURL(blob);
    if (win) win.location.href = url; else window.location.href = url;
    setTimeout(() => URL.revokeObjectURL(url), 120_000);
  } catch (e) { win?.close(); throw e; }
}

export async function uploadFile<T = unknown>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  return api<T>(path, { method: "POST", body: fd });
}

/* Errores de validacion (422 de FastAPI) en lenguaje humano, nunca el JSON crudo. */
const FIELD: Record<string, string> = { email: "correo", password: "contraseña", name: "nombre", phone: "teléfono", amount: "monto", quantity: "cantidad", price: "precio", code: "código", date: "fecha" };
function friendly(items: { loc?: (string | number)[]; msg?: string; type?: string }[]): string {
  const it = items[0] || {};
  const field = String(it.loc?.[it.loc.length - 1] ?? "");
  const label = FIELD[field] || field.replace(/_/g, " ") || "dato";
  if (field === "email" || /email/i.test(it.msg || "")) return "Escribí un correo válido (ejemplo: nombre@empresa.com).";
  if (it.type === "missing") return `Falta el campo ${label}.`;
  if (/at least|too_short/i.test(`${it.type} ${it.msg}`)) return `El campo ${label} es demasiado corto.`;
  if (/greater than|less than|number/i.test(it.msg || "")) return `Revisá el valor de ${label}.`;
  return `Revisá el campo ${label}.`;
}

/** Al abrir el portal: renovar por cookie antes de pedir /me (evita un 401 ruidoso por recarga). */
export const bootstrap = () => (accessToken ? Promise.resolve(true) : tryRefresh());

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown; retry?: boolean } = {}): Promise<T> {
  const { json, retry = true, ...rest } = init;
  const headers: Record<string, string> = { ...previewHeaders(path), ...(rest.headers as Record<string, string>) };
  if (json !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const r = await fetch(`/api${path}`, { ...rest, headers, credentials: "include", body: json !== undefined ? JSON.stringify(json) : rest.body });
  if (r.status === 401 && retry && !path.startsWith("/auth/login")) {
    if (await tryRefresh()) return api<T>(path, { ...init, retry: false });
    auth.set(null);
  }
  if (!r.ok) {
    let msg = r.statusText;
    let data: unknown;
    try { const j = await r.json(); data = j; msg = typeof j.detail === "string" ? j.detail : Array.isArray(j.detail) ? friendly(j.detail) : "No se pudo completar la solicitud"; } catch { /* sin cuerpo */ }
    if (r.status === 403 && r.headers.get("X-2FA-Setup") === "required") window.dispatchEvent(new Event(TWOFA_EVENT));
    throw new ApiError(r.status, msg, r.headers, data);
  }
  if (r.status === 204) return undefined as T;
  return r.json() as Promise<T>;
}

/* ---------- Tipos ---------- */
export type Tenant = { id: number; slug: string; name: string; legal_name: string | null; tax_id: string | null; default_currency: string; logo_url: string | null; plan: string };
export type User = { id: number; email: string; full_name: string; totp_enabled: boolean };
/** ally_company: solo usuarios de una empresa aliada (portal /aliado). */
export type AllyCompanyMe = { id: number; name: string; state: "activo" | "suspendido" | "inactivo"; suspended_reason: string | null; features?: { ganancias: boolean } };
/** twofa: el rol exige verificacion en dos pasos (deadline = fin de la gracia; blocked = vencio y no la activo). */
export type TwoFactorState = { required: boolean; deadline: string | null; blocked: boolean };
export type Me = { user: User; tenant: Tenant; role: string; permissions: Record<string, string[]>; memberships: { tenant: Tenant; role: string }[]; ally_company?: AllyCompanyMe | null; twofa?: TwoFactorState | null };
export type Customer = { id: number; id_type: string; id_number: string | null; name: string; email: string | null; phone: string | null; whatsapp: string | null; currency: string; active: boolean };
/* availability: la disponibilidad de la funcion unica del API (propio / proveedor / fecha), ver ui/Availability */
export type Product = { id: number; name: string; code: string; item_type: string; price: number; currency: string; cabys_code: string | null; unit: string; tax_rate: number | null; category_id: number | null; show_on_web: boolean; tax_ids?: number[]; brand?: string | null; supplier_stock?: number | null; availability?: import("../ui/Availability").Availability | null };
export type Line = { id?: number; product_id: number | null; code: string | null; name: string; description: string | null; cabys_code?: string | null; unit: string; quantity: string; unit_price: string; discount_type: string; discount_value: string; tax_rate: string; subtotal?: string; tax_amount?: string; total?: string; treatment?: string; supplied_by?: string | null };
export type Doc = {
  id: number; number: string; customer_id: number | null; customer_name: string | null; currency: string; fx_sell: string; fx_buy: string;
  issue_date: string; due_date: string | null; discount_type: string; discount_value: string; subtotal: string; discount_total: string; tax_total: string; total: string;
  internal_notes: string | null; external_notes: string | null; external_order: string | null; activity_code: string | null; medical_exemption_card: boolean; status: string; lines: Line[];
  /* vendedor responsable (de quien es la venta y su comision); lo elige solo quien tiene sales.asignar */
  seller_id?: number | null; seller_name?: string | null;
};
export type Quote = Doc & {
  converted_invoice_id: number | null; opportunity?: { id: number; number: string; kind: "venta" | "proyecto"; status: string; parties?: { name: string; role: string }[] } | null;
  /* aceptacion del cliente (aparte de status = emision) y versiones enviadas */
  acceptance_status?: "pendiente" | "aceptada" | "rechazada"; accepted_version?: number | null; current_version?: number | null; has_unsent_changes?: boolean;
  /* proyecto que salio de esta cotizacion: el boton pasa a "Ver PRO-..." (antes "Convertir a proyecto" daba 409) */
  project_id?: number | null; project_number?: string | null;
  /* descuento rechazado por quien aprueba, todavia sin cambios de la vendedora */
  rejection?: { by: string | null; at: string; reason: string | null } | null;
};
export type Payment = { id: number; invoice_id?: number | null; invoice_number?: string | null; customer?: string | null; method: string; kind: string; currency: string; amount: string; tip: string; external_ref: string | null; provider: string; paid_at: string; status: string };
export type Invoice = Doc & { doc_type: string; consecutive: string | null; clave: string | null; balance: string; quote_id: number | null; einvoice_status: string; payments: Payment[]; sale_condition: string; credit_days: number; payment_method: string;
  /* emitida fuera de la plataforma (Fygaro) mientras Hacienda no esta en produccion: numero y fecha de alla */
  external_number?: string | null; external_date?: string | null;
  /* con produccion activa, lo creado antes de activarla se puede marcar emitido en Fygaro (el corte) */
  created_at?: string | null;
  /* cobranza (plan 1.3): cuando le llego al cliente, pausa de la secuencia y recordatorios ya enviados */
  sent_at?: string | null; reminders_paused?: boolean; reminders?: { stage: string; sent_on: string; status: string }[] };
export type DocListItem = { id: number; number: string; customer_name: string | null; currency: string; total: string; balance: string | null; status: string; issue_date: string; due_date: string | null; acceptance_status?: string | null; seller_name?: string | null };
export type Ceo = {
  pipeline: string; pipeline_weighted: string; opportunities: number; receivable: string; sold_month: string;
  /* utilidad del mes: null para quien no ve costos (solo el admin, salvo Ajustes -> Quien ve costos) */
  profit_month: string | null; margin_month: number | null;
  projects_active: number; projects_closed_month: number; projects_without_costs?: number | null; jobs_open: number; jobs_week: number; jobs_late: number;
  quotes_sent: number; quotes_won_month: number; warranties_soon: number; tickets_open?: number | null; tickets_late?: number | null;
};
export type Dashboard = {
  scope?: "mine" | "company";
  gerencia?: Ceo | null;
  pagos: { hoy: string; mes: string; mes_anterior: string; variacion: number | null } | null;
  por_cobrar?: { id: number; number: string; customer: string | null; balance: string; currency: string; due_date: string | null; status: string }[];
  facturado: { hoy: string; mes: string; mes_anterior: string; variacion: number | null };
  pagos_recientes: { id: number; invoice_id: number; ref: string | null; method: string; kind: string; amount: string; currency: string; date: string; status: string }[];
  facturas_recientes: { id: number; number: string; customer: string | null; total: string; balance: string; currency: string; status: string; date: string }[];
  acciones_pendientes: Record<string, number>;
  /* inicio del vendedor: su embudo con la misma cuenta que Oportunidades, en la moneda de la empresa */
  mi_embudo?: { abiertas: number; monto: string; ponderado: string; ganadas_mes: number; ganado_mes: string; perdidas_mes: number; moneda: string; convertido: boolean; sin_tipo_de_cambio?: boolean } | null;
  /* Mis comisiones (vendedor, sin ver_todo): la misma cuenta que /commissions/summary, en colones y sin IVA */
  comisiones?: { ganado_mes: string; pagado_mes: string; por_cobrar: string; moneda: string } | null;
};

/* Levantamiento en resumen para la vendedora (GET /surveys/{id}/summary): solo lectura y sin un solo costo. */
export type SurveySummary = {
  id: number; number: string; kind: string; kind_label: string; customer: string | null; site: string | null; visit_date: string | null;
  technician: string | null; opportunity: { id: number; number: string; title: string } | null;
  status: string; review_label: string | null;
  paso: "borrador" | "revision" | "devuelto" | "costeo" | "cotizado" | "cerrado"; paso_texto: string;
  notes: string | null;
  equipos: { name: string; quantity: string; unit: string; kind: string; note: string | null }[];
  personal: { label: string; people: number; days: string }[];
  puntos: { code: string; label: string | null; kind_label: string; notes: string | null; photos: string[] }[];
  fotos: string[];
  sugerencias_pendientes: { name: string; quantity: string; unit: string | null; kind: string | null; reason: string | null }[];
  cotizacion: { id?: number; number: string; status: string } | null;
};

/* ---------- Helpers ---------- */
/** Marca de tiempo del backend -> Date. Postgres las devuelve con "+00:00" y SQLite (desarrollo) sin zona:
    si no se marcan como UTC, el navegador lee las de SQLite como hora local y el mismo dato se ve distinto
    en cada entorno. Todo lo que guarda la API está en UTC. */
export const parseTs = (s: string) => new Date(/([zZ]|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`);

export const fmtMoney = (v: string | number | null | undefined, currency = "CRC") => {
  const n = Number(v ?? 0);
  return new Intl.NumberFormat("es-CR", { style: "currency", currency, maximumFractionDigits: 2 }).format(n);
};
export const fmtDate = (s: string | null | undefined) => (s ? new Date(s + (s.length === 10 ? "T12:00:00" : "")).toLocaleDateString("es-CR", { day: "2-digit", month: "short", year: "numeric" }) : "—");

export const STATUS: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "info" | "muted" }> = {
  creado: { label: "Creado", tone: "info" },
  enviada: { label: "Enviada", tone: "info" },
  convertida: { label: "Convertida", tone: "ok" },
  pagada: { label: "Pagada", tone: "ok" },
  parcial: { label: "Parcial", tone: "warn" },
  vencida: { label: "Vencida", tone: "bad" },
  anulada: { label: "Anulada", tone: "muted" },
  confirmado: { label: "Confirmado", tone: "ok" },
  pendiente: { label: "Pendiente", tone: "warn" },
  fallido: { label: "Fallido", tone: "bad" },
  // ordenes de tienda
  nuevo: { label: "Nuevo", tone: "info" },
  preparando: { label: "Preparando", tone: "warn" },
  enviado: { label: "Enviado", tone: "info" },
  entregado: { label: "Entregado", tone: "ok" },
  cancelado: { label: "Cancelado", tone: "muted" },
  pagado: { label: "Pagado", tone: "ok" },
  en_revision: { label: "Comprobante en revisión", tone: "warn" },
  listo: { label: "Listo para retiro", tone: "ok" },
  reembolsado: { label: "Reembolsado", tone: "muted" },
  por_aprobar: { label: "Por aprobar", tone: "warn" },
  // campo y proyectos
  borrador: { label: "Borrador", tone: "muted" },
  por_programar: { label: "Por programar", tone: "warn" },
  asignada: { label: "Asignada", tone: "info" },
  en_sitio: { label: "En sitio", tone: "warn" },
  en_proceso: { label: "En proceso", tone: "warn" },
  finalizada: { label: "Finalizada", tone: "ok" },
  planificado: { label: "Planificado", tone: "info" },
  asignado: { label: "Asignado", tone: "info" },
  esperando_cliente: { label: "Esperando al cliente", tone: "muted" },
  resuelto: { label: "Resuelto", tone: "ok" },
  cerrado: { label: "Cerrado", tone: "muted" },
  en_curso: { label: "En curso", tone: "warn" },
  terminado: { label: "Terminado", tone: "ok" },
  facturado: { label: "Facturado", tone: "ok" },
  // aceptacion del cliente (cotizaciones) y revision del supervisor (levantamientos)
  aceptada: { label: "Aceptada", tone: "ok" },
  rechazada: { label: "Rechazada", tone: "bad" },
  aprobado: { label: "Revisión aprobada", tone: "ok" },
  devuelto: { label: "Devuelto", tone: "warn" },
  // recepcion de comprobantes y mensajes ante Hacienda ("simulado" nunca se ve como aceptado)
  por_revisar: { label: "Por revisar", tone: "warn" },
  respondido: { label: "Respondido", tone: "info" },
  sin_respuesta: { label: "Sin respuesta requerida", tone: "muted" },
  ajeno: { label: "No es para la empresa", tone: "muted" },
  invalido: { label: "Rechazado por Hacienda al proveedor", tone: "bad" },
  descartado: { label: "Descartado", tone: "muted" },
  historico: { label: "Histórico (Fygaro)", tone: "muted" },
  simulado: { label: "Simulado", tone: "muted" },
  por_enviar: { label: "Por enviar", tone: "warn" },
  enviando: { label: "Enviando", tone: "info" },
  procesando: { label: "En proceso", tone: "info" },
  aceptado: { label: "Aceptado", tone: "ok" },
  rechazado: { label: "Rechazado", tone: "bad" },
  error: { label: "Error", tone: "bad" },
  sin_emitir: { label: "Sin emitir", tone: "muted" },
  emitida_fuera: { label: "Emitida en Fygaro", tone: "ok" },
  importada: { label: "Importada (histórico)", tone: "muted" },
};
