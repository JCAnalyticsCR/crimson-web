/* Datos de la sesion de la demo compartidos entre mockApi (unico que crea pedidos), la capa y las maquetas.
   - almacen: localStorage/sessionStorage con try/catch y un respaldo EN MEMORIA compartido por todo el bundle: con el
     almacenamiento bloqueado (modo privado, datos de sitio bloqueados) la demo sigue funcionando durante la visita.
   - Pedidos DEMO: los escribe solo mockApi en CLAVES.pedidos. Los cambios de estado que hace «Detrás de escena»
     van aparte, en CLAVES.estados (EstadoDemo por token), y mockApi los superpone al responder «Mi pedido». */
import { CLAVES } from "../registro";

type Area = "local" | "sesion";
const memoria: Record<Area, Map<string, string>> = { local: new Map(), sesion: new Map() };
const nativo = (a: Area): Storage | null => {
  try { return a === "local" ? window.localStorage : window.sessionStorage; } catch { return null; }
};
function area(a: Area) {
  return {
    get(k: string): string | null {
      try { const v = nativo(a)?.getItem(k); if (v !== null && v !== undefined) return v; } catch { /* bloqueado */ }
      return memoria[a].get(k) ?? null;
    },
    set(k: string, v: string): boolean {
      memoria[a].set(k, v);
      try { const s = nativo(a); if (!s) return false; s.setItem(k, v); return true; } catch { return false; }
    },
    del(k: string): void {
      memoria[a].delete(k);
      try { nativo(a)?.removeItem(k); } catch { /* bloqueado */ }
    },
  };
}
export const almacen = { local: area("local"), sesion: area("sesion") };

export const leerJson = <T,>(a: "local" | "sesion", k: string, fallback: T): T => {
  try { const raw = almacen[a].get(k); return raw ? (JSON.parse(raw) as T) : fallback; } catch { return fallback; }
};

// ---------------------------------------------------------------- tipos (contrato con mockApi)
export type EstadoDemo = {
  status: "nuevo" | "en_revision" | "pagado" | "preparando" | "listo" | "enviado" | "entregado" | "cancelado" | "reembolsado";
  paid_at?: string | null; tracking_number?: string | null; carrier?: string | null; cancel_reason?: string | null;
  history: { status: string; label: string; at: string }[]; // historial COMPLETO (reemplaza al del pedido)
};
export type PedidoDemo = {
  token: string; number: string; status: string; status_label: string; created_at: string; paid_at: string | null;
  total: number; subtotal: number; tax_total: number; shipping: number;
  payment_method: string; payment_kind: string; shipping_method: string | null;
  contact: { name: string | null; email: string | null };
  delivery: { type: "envio" | "retiro"; province?: string; canton?: string; district?: string };
  lines: { name: string; quantity: number; total: number; unit_price: number; tax_rate: number; shipping: boolean }[];
  proofs: { filename: string; at: string }[]; history: { status: string; label: string; at: string }[];
  tracking_number: string | null; carrier: string | null; cancel_reason: string | null;
};

/** Aviso interno (capa) de que un pedido de prueba cambio: lo escucha el control de «Mi pedido». */
export const EVENTO_PEDIDOS = "crimson-demo:pedidos-cambio";
export type CambioPedido = { token?: string; estado?: string };
export const avisarCambioPedidos = (detail: CambioPedido = {}) => { try { window.dispatchEvent(new CustomEvent<CambioPedido>(EVENTO_PEDIDOS, { detail })); } catch { /* sin ventana */ } };

/** Etiquetas reales de estado (api/app/services/store_orders.py, STATUS_LABEL). */
export const ETIQUETA_ESTADO: Record<string, string> = {
  nuevo: "Pendiente de pago", en_revision: "Comprobante en revisión", pagado: "Pagado", preparando: "Preparando",
  listo: "Listo para retiro", enviado: "Enviado", entregado: "Entregado", cancelado: "Cancelado", reembolsado: "Reembolsado",
};

type Crudo = Record<string, unknown> & { token: string; number: string };

export const leerPedidosCrudos = (): Record<string, Crudo> => leerJson<Record<string, Crudo>>("sesion", CLAVES.pedidos, {});
export const leerEstados = (): Record<string, EstadoDemo> => leerJson<Record<string, EstadoDemo>>("sesion", CLAVES.estados, {});
export function borrarEstado(token: string) {
  const all = leerEstados();
  if (!(token in all)) return;
  delete all[token];
  almacen.sesion.set(CLAVES.estados, JSON.stringify(all));
  avisarCambioPedidos({ token });
}

/** Superpone el estado escrito por «Detrás de escena» sobre el pedido guardado (lo usa mockApi y leerPedidosDemo). */
export function aplicarEstado<T extends Record<string, unknown>>(pedido: T, e: EstadoDemo | undefined): T {
  if (!e) return pedido;
  const card = pedido.card as { pending?: boolean } | null | undefined;
  const vivo = ["nuevo", "en_revision"].includes(e.status);
  return {
    ...pedido, ...e,
    status_label: ETIQUETA_ESTADO[e.status] || e.status,
    needs_proof: !!pedido.needs_proof && vivo,
    instructions: vivo ? pedido.instructions ?? null : null,
    expires_at: e.status === "nuevo" ? pedido.expires_at ?? null : null,
    card: card && e.status !== "nuevo" ? { ...card, pending: false } : card ?? null,
  };
}

const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const num = (v: unknown): number => Number(v) || 0;

/** Pedidos de prueba de ESTA sesion (los crea el checkout de la demo), con su estado efectivo. Mas reciente primero. */
export function leerPedidosDemo(): PedidoDemo[] {
  const estados = leerEstados();
  return Object.values(leerPedidosCrudos())
    .filter((o) => o && typeof o.token === "string")
    .map((raw) => {
      const o = aplicarEstado(raw, estados[raw.token]);
      const contact = (o.contact || {}) as { name?: string | null; email?: string | null };
      const delivery = (o.delivery || { type: "envio" }) as PedidoDemo["delivery"];
      return {
        token: o.token, number: o.number, status: String(o.status || "nuevo"),
        status_label: ETIQUETA_ESTADO[String(o.status)] || String(o.status_label || o.status || ""),
        created_at: str(o.created_at) || new Date().toISOString(), paid_at: str(o.paid_at),
        total: num(o.total), subtotal: num(o.subtotal), tax_total: num(o.tax_total), shipping: num(o.shipping),
        payment_method: String(o.payment_method || ""), payment_kind: String(o.payment_kind || ""), shipping_method: str(o.shipping_method),
        contact: { name: contact.name ?? null, email: contact.email ?? null },
        delivery: { type: (delivery.type === "retiro" ? "retiro" : "envio") as "retiro" | "envio", province: delivery.province, canton: delivery.canton, district: delivery.district },
        lines: (Array.isArray(o.lines) ? o.lines : []).map((l) => {
          const x = l as Record<string, unknown>;
          return { name: String(x.name || ""), quantity: num(x.quantity), total: num(x.total), unit_price: num(x.unit_price), tax_rate: num(x.tax_rate), shipping: !!x.shipping };
        }),
        proofs: (Array.isArray(o.proofs) ? o.proofs : []) as PedidoDemo["proofs"],
        history: (Array.isArray(o.history) ? o.history : []) as PedidoDemo["history"],
        tracking_number: str(o.tracking_number), carrier: str(o.carrier), cancel_reason: str(o.cancel_reason),
      };
    })
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
}

/** SOLO lo usa «Detrás de escena»: guarda el estado que Crimson le dio a un pedido de prueba. */
export function escribirEstadoDemo(token: string, e: EstadoDemo): void {
  const all = leerEstados();
  all[token] = e;
  almacen.sesion.set(CLAVES.estados, JSON.stringify(all));
  avisarCambioPedidos({ token, estado: e.status });
}
