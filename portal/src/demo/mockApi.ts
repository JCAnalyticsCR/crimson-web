/* DEMOSTRACION ESTATICA: API simulada dentro del navegador.
   Se reemplaza window.fetch SOLO para las direcciones /api/... que usa la vitrina (PublicStore, storeKit,
   StorePages, Checkout, OrderStatus...). Todo se responde desde snapshot.json; NADA sale a un servidor.
   Lo que en la tienda real ESCRIBE (checkout, asesoria, contacto, avisarme, comprobante, pago) aqui no hace nada:
   responde un estado "demo" y avisa a la interfaz (evento DEMO_EVENT) para que muestre el aviso de demostracion.
   Cualquier otro fetch (que la vitrina no hace) pasa al fetch original sin tocarse. */
import { Catalog, DEFAULT_FAQ, DEFAULT_PAGES, INFO_KINDS, type Snapshot } from "./catalog";
import snapshotUrl from "./snapshot.json?url";

export const DEMO_EVENT = "crimson-demo:aviso";
export const DEMO_TEXT = "Esto es una demostración: no se procesan pedidos, pagos ni solicitudes.";
export type DemoNotice = { title: string; text: string };
export const notifyDemo = (n: DemoNotice) => window.dispatchEvent(new CustomEvent<DemoNotice>(DEMO_EVENT, { detail: n }));

const SLUG = "crimson";
const WHATSAPP = "50688889892"; // el publico de crimsoncr.com; la demo NUNCA abre wa.me (DemoShell intercepta el clic)
const ORDERS_KEY = "crimson-demo:pedidos";
const METHODS = [
  { code: "sinpe", label: "SINPE Móvil", needs_proof: true, simulated: false, hint: "Pagás desde la app de tu banco y subís el comprobante." },
  { code: "transferencia", label: "Transferencia bancaria", needs_proof: true, simulated: false, hint: "Transferencia a nuestra cuenta IBAN y subís el comprobante." },
  { code: "tarjeta", label: "Tarjeta (ONVO)", needs_proof: false, simulated: true, hint: "Visa o Mastercard en la página segura de ONVO." },
  { code: "efectivo", label: "Efectivo", needs_proof: false, simulated: false, hint: "Pagás al recibir o al retirar." },
];
// tarifas de ejemplo (colones sin IVA, como se configuran en Mi Tienda -> Envios)
const RATES = [
  { name: "Retiro en Alajuela", zone: "retiro" as const, amount: 0 },
  { name: "Envío GAM", zone: "gam" as const, amount: 3500 },
  { name: "Envío fuera de la GAM", zone: "fuera_gam" as const, amount: 5500 },
];
const PICKUP = { address: "Alajuela, Costa Rica · coordinamos la hora por WhatsApp", hours: "" };
const gross13 = (n: number) => Math.round(n * 1.13 * 100) / 100;
const r2 = (n: number) => Math.round(n * 100) / 100;

let catalogP: Promise<Catalog> | null = null;
const realFetch = window.fetch.bind(window);
function catalog(): Promise<Catalog> {
  if (!catalogP) catalogP = realFetch(snapshotUrl).then((r) => r.json() as Promise<Snapshot>).then((s) => new Catalog(s));
  return catalogP;
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
const fail = (status: number, detail: string) => json({ detail }, status);
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const readJson = async (init?: RequestInit): Promise<Record<string, unknown>> => {
  try { return typeof init?.body === "string" ? JSON.parse(init.body) : {}; } catch { return {}; }
};

type DemoOrder = Record<string, unknown> & { number: string; token: string };
const orders = {
  all(): Record<string, DemoOrder> { try { return JSON.parse(sessionStorage.getItem(ORDERS_KEY) || "{}"); } catch { return {}; } },
  get(t: string) { return this.all()[t] || null; },
  put(o: DemoOrder) { try { sessionStorage.setItem(ORDERS_KEY, JSON.stringify({ ...this.all(), [o.token]: o })); } catch { /* sin almacenamiento: vive solo esta vista */ } },
};
const memOrders = new Map<string, DemoOrder>();

function quote(c: Catalog, body: Record<string, unknown>) {
  const lines = (Array.isArray(body.lines) ? body.lines : []) as { product_id: number; quantity: string | number }[];
  const out: { name: string; quantity: number; total: number; shipping: boolean; unit_price: number; tax_rate: number }[] = [];
  let subtotal = 0, tax = 0;
  for (const l of lines) {
    const p = c.byId.get(Number(l.product_id));
    const q = Math.max(1, Math.min(999, Math.trunc(Number(l.quantity) || 1)));
    if (!p) return { error: "Uno de los productos ya no está disponible." };
    const net = r2(c.net(p) * q);
    const t = r2(net * p.rate / 100);
    subtotal += net; tax += t;
    out.push({ name: p.name, quantity: q, total: r2(net + t), shipping: false, unit_price: c.net(p), tax_rate: p.rate });
  }
  const rate = RATES.find((r) => r.name === body.shipping_method);
  const ship = rate ? rate.amount : 0;
  const shipTax = r2(ship * 0.13);
  tax += shipTax;
  if (rate && ship) out.push({ name: rate.name, quantity: 1, total: r2(ship + shipTax), shipping: true, unit_price: ship, tax_rate: 13 });
  subtotal = r2(subtotal); tax = r2(tax);
  return {
    subtotal, discount_total: 0, tax_total: tax, shipping: ship, total: r2(subtotal + ship + tax), currency: "CRC", coupon: null,
    taxes: [{ rate: "13", amount: tax }], lines: out, free_shipping: false, free_shipping_from: null,
  };
}

const instructions = (code: string, number: string, total: string) => ({
  sinpe: `Vista previa: en la tienda real aquí aparece el número de SINPE Móvil de Crimson, el monto (${total}) y el detalle ${number}.\n${DEMO_TEXT}`,
  transferencia: `Vista previa: en la tienda real aquí aparece la cuenta IBAN de Crimson, el monto (${total}) y el detalle ${number}.\n${DEMO_TEXT}`,
  tarjeta: `Vista previa: en la tienda real aquí se abre la página segura de ONVO para pagar ${total} con tarjeta.\n${DEMO_TEXT}`,
  efectivo: `Vista previa: en la tienda real el pago de ${total} se hace al recibir o al retirar.\n${DEMO_TEXT}`,
} as Record<string, string>)[code] || DEMO_TEXT;

async function route(path: string, sp: URLSearchParams, method: string, init?: RequestInit): Promise<Response> {
  const c = await catalog();
  await wait(90 + Math.random() * 120); // latencia de red creible: los esqueletos de carga se ven como en la real
  const GET = method === "GET";

  if (path === "/public/store-domain") return json({ slug: SLUG });
  const m = /^\/public\/store\/[^/]+(\/.*)?$/.exec(path);
  if (!m) return fail(404, "No encontrado");
  const sub = m[1] || "";

  if (GET && sub === "") {
    const counts = c.counts();
    const tree = c.tree(counts);
    return json({
      store: {
        name: "Crimson", tagline: "Seguridad electrónica, redes y soporte técnico en Costa Rica.", logo_url: `${import.meta.env.BASE_URL}logo.png`,
        primary: "#e2233a", secondary: "#15131a", font: "Manrope", kind: "tienda", whatsapp: WHATSAPP, currency: "CRC",
        legal: { privacy: c.snap.legal.privacy || "", terms: c.snap.legal.terms || "" },
      },
      nav: [],
      categories: tree.map((n) => ({ id: n.id, name: n.name, count: n.count, subcategories: n.children.map((k) => ({ id: k.id, name: k.name, count: k.count })) })),
      product_count: c.prods.length,
      shipping_rates: RATES.map((r) => ({ name: r.name, amount: r.amount, display_amount: gross13(r.amount) })),
      payment_methods: METHODS.map((x) => ({ name: x.label, instructions: "" })),
    });
  }
  if (GET && sub === "/site") return json({ analytics: { ga4_id: null } }); // sin medicion: la demo no carga nada de terceros
  if (GET && sub === "/showcase") return json(c.showcase());
  if (GET && sub === "/catalog") {
    const r = c.catalog(sp);
    return "error" in r ? fail(r.error, r.detail) : json(r.body, 200, { "X-Total-Count": String(r.total) });
  }
  if (GET && sub.startsWith("/item/")) {
    const it = c.item(decodeURIComponent(sub.slice(6)));
    return it ? json({ ...it, whatsapp: WHATSAPP }) : fail(404, "Producto no encontrado");
  }
  if (GET && sub === "/availability") {
    const ids = (sp.get("ids") || "").split(",").map(Number).filter(Boolean);
    return json({ items: ids.map((id) => c.byId.get(id)).filter(Boolean).map((p) => ({ id: p!.id, ...c.card(p!).availability })) });
  }
  if (GET && sub.startsWith("/info/")) {
    const kind = sub.slice(6);
    if (!INFO_KINDS[kind]) return fail(404, "Página no encontrada");
    if (kind === "preguntas-frecuentes") return json({ kind, title: INFO_KINDS[kind], body: "", faq: DEFAULT_FAQ });
    const body = kind === "terminos" ? c.snap.legal.terms : kind === "privacidad" ? c.snap.legal.privacy : DEFAULT_PAGES[kind];
    return body ? json({ kind, title: INFO_KINDS[kind], body, faq: [] }) : fail(404, "Página no disponible");
  }
  if (GET && sub.startsWith("/pages/")) return fail(404, "Página no encontrada");
  if (GET && sub.startsWith("/legacy/")) return fail(404, "No encontrado");
  if (!GET && sub === "/kit") {
    const a = await readJson(init);
    if (Number(a.indoor || 0) + Number(a.outdoor || 0) === 0) return fail(422, "Indique al menos una cámara");
    if (Number(a.indoor || 0) + Number(a.outdoor || 0) > 32) return fail(422, "Para más de 32 cámaras pida una asesoría");
    return json(c.kitResponse(a));
  }

  // ---------- checkout (calcula, pero no crea nada) ----------
  if (GET && sub === "/checkout-config") {
    return json({
      currency: "CRC", methods: METHODS,
      shipping_rates: RATES.map((r) => ({ name: r.name, zone: r.zone, per_kg: false, display_amount: gross13(r.amount) })),
      provinces: c.snap.provinces, outside_gam: c.snap.outside_gam, terms_required: !!c.snap.legal.terms,
      pickup: PICKUP, free_shipping_from: null, auto_invoice: true, whatsapp: WHATSAPP,
    });
  }
  if (!GET && sub === "/quote") {
    const b = await readJson(init);
    if (b.coupon_code) return fail(422, `${DEMO_TEXT} Los cupones no se validan en la vista previa.`);
    const q = quote(c, b);
    return "error" in q ? fail(409, q.error as string) : json(q);
  }
  if (!GET && sub === "/checkout") {
    const b = await readJson(init);
    const q = quote(c, b);
    if ("error" in q) return fail(409, q.error as string);
    const code = String(b.payment_method || "sinpe");
    const meth = METHODS.find((x) => x.code === code) || METHODS[0];
    const number = `DEMO-${String(1000 + Math.floor(Math.random() * 9000))}`;
    const token = `demo-${number.slice(5)}-${Math.random().toString(36).slice(2, 8)}`;
    const contact = (b.contact || {}) as { name?: string; email?: string };
    const delivery = (b.delivery || { type: "envio" }) as { type: "envio" | "retiro"; province?: string; canton?: string; district?: string };
    const totalTxt = new Intl.NumberFormat("es-CR", { style: "currency", currency: "CRC", currencyDisplay: "narrowSymbol", maximumFractionDigits: 0 }).format(q.total);
    const now = new Date().toISOString();
    const order: DemoOrder = {
      token, number, status: "nuevo", status_label: "Pedido de demostración", created_at: now, paid_at: null, expires_at: null, currency: "CRC",
      subtotal: q.subtotal, discount_total: 0, shipping: q.shipping, tax_total: q.tax_total, total: q.total, coupon_code: null,
      lines: q.lines, contact: { name: contact.name || null, email: contact.email || null }, shipping_method: b.shipping_method || null,
      delivery: { type: delivery.type, province: delivery.province, canton: delivery.canton, district: delivery.district },
      pickup: delivery.type === "retiro" ? PICKUP : null, payment_method: meth.label, payment_kind: meth.code,
      instructions: instructions(meth.code, number, totalTxt), needs_proof: meth.needs_proof, proofs: [],
      card: meth.code === "tarjeta" ? { pending: true, simulated: true } : null, tracking_number: null, carrier: null, cancel_reason: null,
      history: [{ status: "nuevo", label: "Pedido de demostración", at: now }], whatsapp: WHATSAPP, store_name: "Crimson",
    };
    memOrders.set(token, order);
    orders.put(order);
    notifyDemo({ title: `Pedido de demostración ${number}`, text: `${DEMO_TEXT} No se creó ningún pedido, no se cobró nada y no se envió ningún correo. En la tienda real, aquí el cliente ve su número de pedido, cómo pagar y el avance.` });
    return json({
      number, status: "nuevo", total: q.total, currency: "CRC", payment_kind: meth.code, payment_method: meth.label, instructions: order.instructions,
      needs_proof: meth.needs_proof, whatsapp: WHATSAPP, track_token: token, track_url: "", lines: q.lines,
      email: contact.email ? { to: "(demostración: no se envió ningún correo)", status: "demo" } : null, pay: null, duplicate: false,
    }, 201);
  }
  const om = /^\/orders\/([^/]+)(\/.*)?$/.exec(sub);
  if (om && om[1] === "lookup") return fail(404, `${DEMO_TEXT} No hay pedidos reales para buscar.`);
  if (om && GET && !om[2]) {
    const t = decodeURIComponent(om[1]);
    const o = memOrders.get(t) || orders.get(t);
    return o ? json(o) : fail(404, "No encontramos ese pedido.");
  }
  if (om && !GET) {
    notifyDemo({ title: "Vista previa", text: `${DEMO_TEXT} En la tienda real aquí se ${om[2] === "/proof" ? "sube el comprobante de pago" : "procesa el pago con tarjeta"}.` });
    return fail(409, DEMO_TEXT);
  }

  // ---------- formularios: no se envia nada ----------
  if (!GET && sub === "/asesoria") {
    notifyDemo({ title: "Solicitud de demostración", text: `${DEMO_TEXT} En la tienda real, esta solicitud crea una oportunidad en el CRM de Crimson y avisa al equipo.` });
    return json({ ok: true, message: `${DEMO_TEXT} En la tienda real, un asesor de Crimson le contacta en horario hábil.` });
  }
  if (!GET && (sub === "/contact" || sub === "/stock-alert")) {
    notifyDemo({ title: "Vista previa", text: `${DEMO_TEXT} En la tienda real este formulario llega al equipo de Crimson.` });
    return json({ ok: true, demo: true }, 201);
  }
  return fail(404, "No disponible en la demostración");
}

/** Instala el interceptor. Idempotente. */
export function installMockApi() {
  const w = window as Window & { __crimsonDemoApi?: boolean };
  if (w.__crimsonDemoApi) return;
  w.__crimsonDemoApi = true;
  catalog(); // la descarga del snapshot arranca ya, mientras se escribe el PIN
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, location.href);
    if (url.origin === location.origin && url.pathname.startsWith("/api/")) {
      const method = (init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
      try { return await route(url.pathname.slice(4), url.searchParams, method, init); }
      catch { return fail(500, "No se pudo cargar"); }
    }
    return realFetch(input as RequestInfo, init);
  };
}
