/* Cliente HTTP: token de acceso en memoria, refresh automatico por cookie httpOnly, errores tipados. */

export class ApiError extends Error {
  constructor(public status: number, message: string, public headers?: Headers) {
    super(message);
  }
}

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
  const headers: Record<string, string> = {};
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

/** Al abrir el portal: renovar por cookie antes de pedir /me (evita un 401 ruidoso por recarga). */
export const bootstrap = () => (accessToken ? Promise.resolve(true) : tryRefresh());

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown; retry?: boolean } = {}): Promise<T> {
  const { json, retry = true, ...rest } = init;
  const headers: Record<string, string> = { ...(rest.headers as Record<string, string>) };
  if (json !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const r = await fetch(`/api${path}`, { ...rest, headers, credentials: "include", body: json !== undefined ? JSON.stringify(json) : rest.body });
  if (r.status === 401 && retry && !path.startsWith("/auth/login")) {
    if (await tryRefresh()) return api<T>(path, { ...init, retry: false });
    auth.set(null);
  }
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch { /* sin cuerpo */ }
    throw new ApiError(r.status, msg, r.headers);
  }
  if (r.status === 204) return undefined as T;
  return r.json() as Promise<T>;
}

/* ---------- Tipos ---------- */
export type Tenant = { id: number; slug: string; name: string; legal_name: string | null; tax_id: string | null; default_currency: string; logo_url: string | null; plan: string };
export type User = { id: number; email: string; full_name: string; totp_enabled: boolean };
export type Me = { user: User; tenant: Tenant; role: string; permissions: Record<string, string[]>; memberships: { tenant: Tenant; role: string }[] };
export type Customer = { id: number; id_type: string; id_number: string | null; name: string; email: string | null; phone: string | null; whatsapp: string | null; currency: string; active: boolean };
export type Product = { id: number; name: string; code: string; item_type: string; price: number; currency: string; cabys_code: string | null; unit: string; tax_rate: number | null; category_id: number | null; show_on_web: boolean };
export type Line = { id?: number; product_id: number | null; code: string | null; name: string; description: string | null; cabys_code?: string | null; unit: string; quantity: string; unit_price: string; discount_type: string; discount_value: string; tax_rate: string; subtotal?: string; tax_amount?: string; total?: string };
export type Doc = {
  id: number; number: string; customer_id: number | null; customer_name: string | null; currency: string; fx_sell: string; fx_buy: string;
  issue_date: string; due_date: string | null; discount_type: string; discount_value: string; subtotal: string; discount_total: string; tax_total: string; total: string;
  internal_notes: string | null; external_notes: string | null; external_order: string | null; activity_code: string | null; medical_exemption_card: boolean; status: string; lines: Line[];
};
export type Quote = Doc & { converted_invoice_id: number | null };
export type Payment = { id: number; method: string; kind: string; currency: string; amount: string; tip: string; external_ref: string | null; provider: string; paid_at: string; status: string };
export type Invoice = Doc & { doc_type: string; consecutive: string | null; clave: string | null; balance: string; quote_id: number | null; einvoice_status: string; payments: Payment[]; sale_condition: string; credit_days: number; payment_method: string };
export type DocListItem = { id: number; number: string; customer_name: string | null; currency: string; total: string; balance: string | null; status: string; issue_date: string; due_date: string | null };
export type Dashboard = {
  scope?: "mine" | "company";
  pagos: { hoy: string; mes: string; mes_anterior: string; variacion: number | null };
  facturado: { hoy: string; mes: string; mes_anterior: string; variacion: number | null };
  pagos_recientes: { id: number; invoice_id: number; ref: string | null; method: string; kind: string; amount: string; currency: string; date: string; status: string }[];
  facturas_recientes: { id: number; number: string; customer: string | null; total: string; balance: string; currency: string; status: string; date: string }[];
  acciones_pendientes: Record<string, number>;
};

/* ---------- Helpers ---------- */
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
};
