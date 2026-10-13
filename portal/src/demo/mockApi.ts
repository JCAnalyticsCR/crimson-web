/* DEMOSTRACION ESTATICA: API simulada dentro del navegador.
   Se reemplaza window.fetch SOLO para las direcciones /api/... que usa la vitrina (PublicStore, storeKit,
   StorePages, Checkout, OrderStatus...). Todo se responde desde snapshot.json; NADA sale a un servidor.
   Lo que en la tienda real ESCRIBE (checkout, asesoria, contacto, avisarme, comprobante, pago) aqui no hace nada:
   responde un estado "demo" y avisa a la interfaz (evento DEMO_EVENT) para que muestre el aviso de demostracion.
   Cualquier otro fetch (que la vitrina no hace) pasa al fetch original sin tocarse.

   «Mi pedido» de la demo:
   - ejemplo-<estado> (nuevo, en_revision, pagado, preparando, enviado, entregado): el pedido de ejemplo ORD-000128 de
     María, armado con quote() y con fechas relativas a ahora, como si cada estado acabara de pasar.
   - demo-*: los pedidos de prueba de esta sesion (sessionStorage), con el estado que les dio «Detrás de escena»
     (crimson-demo:estados) superpuesto. El comprobante nunca se lee ni se sube: solo se toma el nombre del archivo.
   - El correo de confirmacion queda "demo" (no "enviado"): seria falso decir que salio. */
import { Catalog, DEFAULT_FAQ, DEFAULT_PAGES, INFO_KINDS, type Snapshot } from "./catalog";
import snapshotUrl from "./snapshot.json?url";
import { CLAVES } from "./propuestas/registro";
import { ETIQUETA_ESTADO, almacen, aplicarEstado, borrarEstado, leerEstados, leerPedidosCrudos } from "./propuestas/_comun/pedidos";

export const DEMO_EVENT = "crimson-demo:aviso";
export const DEMO_TEXT = "Esto es una demostración: no se procesan pedidos, pagos ni solicitudes.";
export type DemoNotice = { title: string; text: string };
export const notifyDemo = (n: DemoNotice) => window.dispatchEvent(new CustomEvent<DemoNotice>(DEMO_EVENT, { detail: n }));

const SLUG = "crimson";
const WHATSAPP = "50688889892"; // el publico de crimsoncr.com; la demo NUNCA abre wa.me (DemoShell intercepta el clic)
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
// almacen.sesion tiene respaldo en memoria: sin almacenamiento, los pedidos viven mientras dure la visita
const orders = {
  all(): Record<string, DemoOrder> { return leerPedidosCrudos() as Record<string, DemoOrder>; },
  get(t: string) { return this.all()[t] || null; },
  put(o: DemoOrder) { almacen.sesion.set(CLAVES.pedidos, JSON.stringify({ ...this.all(), [o.token]: o })); },
};
const memOrders = new Map<string, DemoOrder>();
const pedidoDemo = (t: string): DemoOrder | null => memOrders.get(t) || orders.get(t);
const guardar = (o: DemoOrder) => { memOrders.set(o.token, o); orders.put(o); };
const crc = (n: number) => new Intl.NumberFormat("es-CR", { style: "currency", currency: "CRC", currencyDisplay: "narrowSymbol", maximumFractionDigits: 0 }).format(n);

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

// ---------- pedido de ejemplo ORD-000128 (María Rodríguez, SINPE Móvil, Envío GAM a San Rafael de Alajuela) ----------
const ESTADOS_EJEMPLO = ["nuevo", "en_revision", "pagado", "preparando", "enviado", "entregado"] as const;
const MIN = 60_000, HORA = 60 * MIN;
const DESFASE: Record<string, number> = { nuevo: 0, en_revision: 25 * MIN, pagado: 2 * HORA, preparando: 22 * HORA, enviado: 46 * HORA, entregado: 70 * HORA };
const LINEAS_EJEMPLO = [{ product_id: 53, quantity: 2 }, { product_id: 177, quantity: 1 }, { product_id: 11, quantity: 1 }];

function pedidoEjemplo(c: Catalog, estado: string, comprobante?: { filename: string; at: string }): DemoOrder | null {
  const i = (ESTADOS_EJEMPLO as readonly string[]).indexOf(estado);
  if (i < 0) return null;
  const q = quote(c, { lines: LINEAS_EJEMPLO, shipping_method: "Envío GAM" });
  if ("error" in q) return null;
  const creado = Date.now() - (DESFASE[estado] + 15 * MIN); // cada estado, como si acabara de pasar
  const at = (k: string) => new Date(creado + DESFASE[k]).toISOString();
  const vivo = estado === "nuevo" || estado === "en_revision";
  const proofs = i >= 1 ? [{ filename: "comprobante-sinpe-ejemplo.jpg", at: at("en_revision") }] : [];
  if (comprobante) proofs.push(comprobante);
  return {
    token: `ejemplo-${estado}`, number: "ORD-000128", status: estado, status_label: ETIQUETA_ESTADO[estado],
    created_at: at("nuevo"), paid_at: i >= 2 ? at("pagado") : null, expires_at: estado === "nuevo" ? new Date(creado + 24 * HORA).toISOString() : null,
    currency: "CRC", subtotal: q.subtotal, discount_total: 0, shipping: q.shipping, tax_total: q.tax_total, total: q.total, coupon_code: null,
    lines: q.lines, contact: { name: "María Rodríguez", email: "maria@example.com" }, shipping_method: "Envío GAM",
    delivery: { type: "envio", province: "Alajuela", canton: "Alajuela", district: "San Rafael" }, pickup: null,
    payment_method: "SINPE Móvil", payment_kind: "sinpe",
    instructions: vivo ? instructions("sinpe", "ORD-000128", crc(q.total)) : null, needs_proof: vivo, proofs,
    card: null, tracking_number: i >= 4 ? "EJ012844710CR" : null, carrier: i >= 4 ? "Correos de Costa Rica" : null, cancel_reason: null,
    history: ESTADOS_EJEMPLO.slice(0, i + 1).map((k) => ({ status: k, label: ETIQUETA_ESTADO[k], at: at(k) })),
    whatsapp: WHATSAPP, store_name: "Crimson",
  };
}

/** Pedido de prueba con el estado de «Detrás de escena» ya fusionado (para escribir sobre el y borrar el overlay). */
function pedidoDemoVigente(t: string): DemoOrder | null {
  const o = pedidoDemo(t);
  return o ? aplicarEstado(o, leerEstados()[t]) : null;
}
const nombreArchivo = (init?: RequestInit): string => {
  const f = init?.body instanceof FormData ? init.body.get("file") : null;
  const n = f && typeof f === "object" && "name" in f ? String((f as File).name || "") : "";
  return (n || "comprobante.jpg").slice(0, 120);
};

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
  if (GET && sub === "/showcase") {
    // titular y bajada que se prueban en «Detrás de escena» → Mi Tienda → Vitrina (solo esta sesion)
    let v: { headline?: unknown; subline?: unknown } = {};
    try { v = JSON.parse(almacen.sesion.get(CLAVES.vitrina) || "{}") || {}; } catch { v = {}; }
    const headline = typeof v.headline === "string" ? v.headline.trim().slice(0, 120) : "";
    const subline = typeof v.subline === "string" ? v.subline.trim().slice(0, 300) : "";
    return json({ ...c.showcase(), headline, subline });
  }
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
      token, number, status: "nuevo", status_label: ETIQUETA_ESTADO.nuevo, created_at: now, paid_at: null, expires_at: null, currency: "CRC",
      subtotal: q.subtotal, discount_total: 0, shipping: q.shipping, tax_total: q.tax_total, total: q.total, coupon_code: null,
      lines: q.lines, contact: { name: contact.name || null, email: contact.email || null }, shipping_method: b.shipping_method || null,
      delivery: { type: delivery.type, province: delivery.province, canton: delivery.canton, district: delivery.district },
      pickup: delivery.type === "retiro" ? PICKUP : null, payment_method: meth.label, payment_kind: meth.code,
      instructions: instructions(meth.code, number, totalTxt), needs_proof: meth.needs_proof, proofs: [],
      card: meth.code === "tarjeta" ? { pending: true, simulated: true } : null, tracking_number: null, carrier: null, cancel_reason: null,
      history: [{ status: "nuevo", label: ETIQUETA_ESTADO.nuevo, at: now }], whatsapp: WHATSAPP, store_name: "Crimson",
    };
    guardar(order);
    notifyDemo({ title: `Pedido de demostración ${number}`, text: `${DEMO_TEXT} No se creó ningún pedido, no se cobró nada y no se envió ningún correo. En la tienda real, aquí el cliente ve su número de pedido, cómo pagar y el avance.` });
    return json({
      number, status: "nuevo", total: q.total, currency: "CRC", payment_kind: meth.code, payment_method: meth.label, instructions: order.instructions,
      needs_proof: meth.needs_proof, whatsapp: WHATSAPP, track_token: token, track_url: "", lines: q.lines,
      email: contact.email ? { to: "(demostración: no se envió ningún correo)", status: "demo" } : null, pay: null, duplicate: false,
    }, 201);
  }
  const om = /^\/orders\/([^/]+)(\/.*)?$/.exec(sub);
  if (om && om[1] === "lookup" && !GET) {
    // ORD-000128 (el pedido de ejemplo) con cualquier correo, o un pedido de prueba DEMO-xxxx de esta sesion
    const b = await readJson(init);
    const num = String(b.number || "").trim();
    if (/^ord-?0*128$/i.test(num)) return json({ token: "ejemplo-enviado" });
    const propio = [...memOrders.values(), ...Object.values(orders.all())].find((o) => String(o.number).toUpperCase() === num.toUpperCase());
    if (propio) return json({ token: propio.token });
    return fail(404, "No encontramos ese pedido. En la vista previa probá con el pedido de ejemplo ORD-000128 y cualquier correo.");
  }
  if (om && GET && !om[2]) {
    const t = decodeURIComponent(om[1]);
    if (t.startsWith("ejemplo-")) { const ej = pedidoEjemplo(c, t.slice(8)); return ej ? json(ej) : fail(404, "No encontramos ese pedido."); }
    const o = pedidoDemoVigente(t);
    return o ? json(o) : fail(404, "No encontramos ese pedido.");
  }
  if (om && !GET && om[2] === "/proof") {
    const t = decodeURIComponent(om[1]);
    const comprobante = { filename: nombreArchivo(init), at: new Date().toISOString() }; // el archivo no se lee ni sale del navegador
    const aviso = () => notifyDemo({ title: "Vista previa", text: "El comprobante no salió de tu navegador. En la tienda real solo lo ve el equipo de Crimson." });
    if (t === "ejemplo-nuevo" || t === "ejemplo-en_revision") {
      const ej = pedidoEjemplo(c, "en_revision", comprobante);
      if (!ej) return fail(404, "No encontramos ese pedido.");
      if (t === "ejemplo-nuevo") ej.proofs = [comprobante];
      aviso();
      return json(ej);
    }
    const o = t.startsWith("demo-") ? pedidoDemoVigente(t) : null;
    if (!o) return fail(404, "No encontramos ese pedido.");
    const upd: DemoOrder = {
      ...o, status: "en_revision", status_label: ETIQUETA_ESTADO.en_revision,
      proofs: [...((o.proofs as unknown[]) || []), comprobante],
      history: [...((o.history as unknown[]) || []), { status: "en_revision", label: ETIQUETA_ESTADO.en_revision, at: comprobante.at }],
    };
    guardar(upd);
    borrarEstado(t);
    aviso();
    return json(upd);
  }
  if (om && !GET && om[2] === "/simulate-payment" && om[1].startsWith("demo-")) {
    const t = decodeURIComponent(om[1]);
    const o = pedidoDemoVigente(t);
    if (!o) return fail(404, "No encontramos ese pedido.");
    const now = new Date().toISOString();
    const upd: DemoOrder = {
      ...o, status: "pagado", status_label: ETIQUETA_ESTADO.pagado, paid_at: now, expires_at: null, instructions: null, needs_proof: false,
      card: { pending: false, simulated: true },
      history: [...((o.history as unknown[]) || []), { status: "pagado", label: ETIQUETA_ESTADO.pagado, at: now }],
    };
    guardar(upd);
    borrarEstado(t);
    return json(upd);
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
