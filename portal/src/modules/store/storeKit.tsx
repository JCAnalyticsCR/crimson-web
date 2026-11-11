/* Piezas compartidas de la vitrina: tipos, contexto de la tienda, precios, disponibilidad, tarjeta de producto,
   marco de visor (estetica "centro de monitoreo" de crimsoncr.com) y metas SEO del lado del navegador.
   El carrito NO vive aqui: la vitrina solo llama a ctx.addToCart (lo provee PublicStore / el modulo de pedidos). */
/* QUIEN MAS USA ESTO, FUERA DE LA TIENDA (revisado el 10/11 con grep; tarea O0-02 del plan de la tienda).
   `tsc -b` compila todo src/ con noUnusedLocals: renombrar algo, cambiar las props de un componente o quitar una clase
   aqui puede poner el CI en rojo o mover una pantalla que no es la vitrina. Antes de tocar, buscar el nombre en src/.
   - storeKit.tsx: ademas de la vitrina (PublicStore, StoreHome, CategoryPage, ProductPage, KitBuilder, StorePages,
     Checkout) lo importa la demo de src/demo/propuestas: venta/A1Instalacion.tsx (AvailBadge, IC, Ico,
     PriceTag, money), venta/A3Cotizacion.tsx (IC, Ico, PriceTag, money), venta/piezas.tsx (IC, Ico, Viewfinder) y
     cliente/piezas.tsx (IC).
   - catalogFilter.ts: CategoryPage, ProductPage, StoreHome y useSnapshot.ts; toma de aqui los tipos Avail y VProd.
     Sus tipos del snapshot los usa tambien la tienda de pruebas de Playwright (portal/e2e/app/api.ts).
   - shop.css y vitrina.css: PublicStore y, en la demo, propuestas/venta/A1Instalacion.tsx y A3Cotizacion.tsx,
     propuestas/cliente/B2Plan.tsx (shop.css + checkout.css) y vip/preview/FakeStore.tsx.
   - checkout.css: Checkout.tsx, OrderStatus.tsx y B2Plan.tsx. orders.css: Orders.tsx, CheckoutSettings.tsx,
     modules/support/Warranties.tsx y la demo (propuestas/gestion/MiTienda.tsx y Pedidos.tsx).
   - shopkit.tsx (otro archivo, con k minuscula): Checkout.tsx y OrderStatus.tsx. OrderStatus es la pantalla que abre
     el enlace «?pedido=<token>» de los correos de pedido (api/app/services/store_orders.py, api/app/tasks/correo.py):
     esa direccion y lo que muestra no se cambian sin revisar los correos ya enviados.
   - TestShop.tsx (la «Tienda de pruebas» del panel) NO usa nada de esto: tiene su hoja (test-shop.css) y los
     componentes del panel; no sirve para probar la vitrina ni el checkout.
   - src/demo/mockApi.ts y catalog.ts imitan las respuestas del API publico que estas piezas leen; los usan la demo y
     las pruebas de pantalla (portal/e2e). Un campo nuevo que la vitrina necesite tiene que poder faltar sin romper. */
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { mensajePedido } from "./pedidoWa";
import { conNombreVisible } from "./storeNames";

/** La disponibilidad viene del API (services/availability, audiencia publico): state en_bodega | bajo_pedido |
    consultar | agotado | servicio, con "ultimas", la cantidad (solo si la tienda la publica) y max_qty (tope en linea).
    blocked: no se puede comprar en linea (disponible === false): se ofrece "Consultar por WhatsApp".
    accion (O1-02): lo que se ofrece cuando no se compra en linea (whatsapp | avisarme | ninguna; comprar si se vende).
    label_short: la etiqueta corta de la tarjeta (aditiva en el API; si falta, el mapa de respaldo SHORT). */
export type Avail = { state: string; label: string; label_short?: string | null; accion?: string | null; max_qty?: number | null; blocked?: boolean; cantidad?: number | null; ultimas?: boolean; disponible?: boolean };
export type Variant = { id: number; name: string; price: string | number; display_price?: string | number };
export type VProd = {
  id: number; code: string; name: string; brand: string | null; model?: string | null; image: string | null; images: string[];
  description: string | null; category: string | null; category_id: number | null; category_path?: string | null; path: string; type?: string;
  tax_rate: number; item_type: string; display_price: string | number; display_currency: string; compare_display_price?: string | number | null;
  price_on_request?: boolean; availability: Avail; variants: Variant[]; price: string | number; currency: string;
  /** O2-06 (aditivo): datos clave de la tarjeta (api sv.key_specs); el primero distingue a la familia */
  ks?: string[] | null;
  /** fecha de alta (unix, segundos): «Nuevo» en la tarjeta hasta 30 dias */
  created_ts?: number | null;
  /** O3-01/O3-02: nombre comercial aprobado y codigo publico (storeNames.ts); name y code son los fiscales */
  display_name?: string | null; public_code?: string | null;
};
/** Datos del comercio (O1-17, Mi Tienda -> Datos del comercio). Todo opcional: vacio = no se muestra. */
export type Business = { legal_name?: string; legal_id?: string; address?: string; phone?: string; email?: string; hours?: string };
/** las lineas del pie con los datos del comercio: solo lo que este lleno, nunca una etiqueta vacia */
export function businessLines(b?: Business | null): string[] {
  if (!b) return [];
  const t = (v?: string) => (v || "").trim();
  const quien = [t(b.legal_name), t(b.legal_id) && `Cédula jurídica ${t(b.legal_id)}`, t(b.address)].filter(Boolean).join(" · ");
  const contacto = [t(b.phone) && `Tel. ${t(b.phone)}`, t(b.email), t(b.hours)].filter(Boolean).join(" · ");
  return [quien, contacto].filter(Boolean);
}
export type HomeStore = { name: string; tagline: string; logo_url: string | null; primary: string; secondary: string; font: string; kind: string; whatsapp: string; currency: string; legal: { privacy: string; terms: string }; business?: Business; trust_ok?: boolean; /** O4-06: tema de Mi Tienda -> Apariencia (null = el de hoy) */ theme?: Theme | null };

/** Envio visible antes del checkout (O1-09, GET /public/store/{slug} -> shipping_public). Todo derivado de Mi Tienda;
    montos con IVA en la moneda de la tienda. Aditivo: un API viejo no lo manda y no se muestra nada. */
export type ShipRate = { id?: string | null; name: string; zone?: string | null; area: string; amount: number | string; lead?: string | null };
export type ShipPub = { free_from: number | string | null; rates: ShipRate[]; pickup: { address: string; hours?: string } | null; invoice?: boolean };
/** texto de una tarifa: «₡3.955 a todo el país (Correos de Costa Rica)» y, si la tarifa lo trae, su plazo */
export const rateText = (r: ShipRate, cur: string) => `${money(r.amount, cur)} ${r.area}${r.lead ? ` en ${r.lead}` : ""} (${r.name})`;
/** lo que falta para el envio gratis (0 = ya lo tiene); null = la tienda no ofrece envio gratis */
export function freeShipGap(s: ShipPub | null | undefined, total: number): number | null {
  const from = Number(s?.free_from || 0);
  if (!from) return null;
  return Math.max(0, Math.round((from - total) * 100) / 100);
}
/** O1-09: mientras la tienda no tenga direccion de retiro no se promete «retiro» en ningun texto de la vitrina */
export const sinRetiro = (s: ShipPub | null | undefined) => !!s && !s.pickup; // sin el dato (API viejo) no se toca nada
export function quitarRetiro(texto: string): string {
  if (/^\s*envío o retiro\s*$/i.test(texto)) return "Envío a todo el país";
  return texto.replace(/\s*(o|y)\s+retiro( coordinado| en (tienda|sitio))?/gi, "");
}

/** O1-09: sin direccion de retiro, la pagina de envios no promete retiro (lo quita de cada linea que lo menciona). El
    texto vive en el API (DEFAULT_PAGES, archivo de la vitrina): el filtro va aqui, en el navegador. */
export function sinParrafosDeRetiro(body: string): string {
  return body.split("\n").map((l) => {
    if (!/retir/i.test(l)) return l;
    const frases = (l.match(/[^.!?]+[.!?]*\s*/g) || [l]).filter((f) => !/retir/i.test(f));
    return quitarRetiro(frases.join("").trim());
  }).filter((l, i, all) => l !== "" || all[i - 1] !== "").join("\n");
}

/** Envio en la ficha (O1-09), solo en lo comprable y bajo el precio: gratis si el precio alcanza el umbral; si no, las
    tarifas completas con su zona (nunca «desde») y el envio gratis desde cuanto. */
export function ShipInfo({ price, cur }: { price: number; cur: string }) {
  const { shipping: s } = useStore();
  if (!s || (!s.rates.length && !s.free_from)) return null;
  const gap = freeShipGap(s, price);
  if (gap === 0) return <p className="vt-ship"><Ico d={IC.truck} size={16} /><span><b>Envío gratis</b> a todo el país</span></p>;
  const lead = s.rates.some((r) => !r.lead);
  return (
    <p className="vt-ship">
      <Ico d={IC.truck} size={16} />
      <span>
        Envío: {s.rates.map((r) => rateText(r, cur)).join(" · ")}{s.free_from ? `${s.rates.length ? " · " : ""}gratis desde ${money(s.free_from, cur)}` : ""}.
        {lead && <small> El plazo de entrega te lo confirmamos al despachar.</small>}
      </span>
    </p>
  );
}

export type StoreCtx = {
  slug: string;
  base: string; // "" en el dominio de la tienda, "/tienda/<slug>" dentro del portal
  hostMode: boolean;
  store: HomeStore;
  isShop: boolean;
  api: <T>(path: string, init?: RequestInit) => Promise<T>;
  addToCart: (p: VProd, qty?: number, variant?: Variant) => void;
  openCart: () => void;
  compare: number[];
  toggleCompare: (id: number) => void;
  /** O1-09: envio publico (puede faltar con un API viejo) */
  shipping?: ShipPub | null;
  /** O2-07: cuantas unidades de ese producto hay en el carrito (la ficha lo dice bajo el boton) */
  cartQty?: (id: number) => number;
  /** O2-07: los medios de pago activos en palabras («Tarjeta, SINPE o transferencia»); vacio = no se dice nada */
  payText?: string;
  /** O1-00 (aditivo, apagado por defecto): con el candado legal se publican garantia y devoluciones */
  trustOk?: boolean;
};
/** «Tarjeta, SINPE o transferencia» a partir de los nombres de los metodos de pago activos */
export function payWords(names: string[]): string {
  const out: string[] = [];
  const add = (w: string) => { if (!out.includes(w)) out.push(w); };
  for (const n of names) {
    const f = n.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
    if (/tarjeta|onvo|card/.test(f)) add("Tarjeta");
    else if (/sinpe/.test(f)) add("SINPE");
    else if (/transfer/.test(f)) add("transferencia");
    else if (/efectivo/.test(f)) add("efectivo");
  }
  const w = out.map((x, i) => (i === 0 ? x[0].toUpperCase() + x.slice(1) : x));
  return w.length > 1 ? `${w.slice(0, -1).join(", ")} o ${w[w.length - 1]}` : w[0] || "";
}
const Ctx = createContext<StoreCtx | null>(null);
export const StoreProvider = ({ value, children }: { value: StoreCtx; children: ReactNode }) => <Ctx.Provider value={value}>{children}</Ctx.Provider>;
export function useStore(): StoreCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useStore fuera de la tienda");
  return c;
}

export const money = (v: string | number, cur = "CRC") =>
  new Intl.NumberFormat("es-CR", { style: "currency", currency: cur, currencyDisplay: "narrowSymbol", minimumFractionDigits: cur === "CRC" ? 0 : 2, maximumFractionDigits: cur === "CRC" ? 0 : 2 }).format(Number(v) || 0);
/** O2-04: el monto con el simbolo de la moneda en su propio span (.vt-cur). El texto es identico al de money(). */
export function Money({ v, cur = "CRC" }: { v: string | number; cur?: string }) {
  const parts = new Intl.NumberFormat("es-CR", { style: "currency", currency: cur, currencyDisplay: "narrowSymbol", minimumFractionDigits: cur === "CRC" ? 0 : 2, maximumFractionDigits: cur === "CRC" ? 0 : 2 }).formatToParts(Number(v) || 0);
  return <>{parts.map((x, i) => (x.type === "currency" ? <span key={i} className="vt-cur">{x.value}</span> : x.value))}</>;
}
/** direccion completa de una ruta de la tienda (para mensajes que se mandan fuera de la pagina) */
export const absUrl = (path: string) => (typeof location !== "undefined" ? `${location.origin}${path}` : path);
export const waLink = (phone: string, text: string) => `https://wa.me/${phone.replace(/\D/g, "")}?text=${encodeURIComponent(text)}`;
export const calm = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
export const safeStore = {
  get(k: string) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k: string, v: string) { try { localStorage.setItem(k, v); } catch { /* modo privado */ } },
};
/** mismo algoritmo que services/store_showcase.slugify (las direcciones las arma el API; esto es para el menu) */
export function slugify(s: string, limit = 60): string {
  let t = s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  if (t.length > limit) { const cut = t.slice(0, limit); t = cut.includes("-") ? cut.slice(0, cut.lastIndexOf("-")) : cut; }
  return t || "producto";
}
export function catSlugs(cats: { id: number; name: string; slug?: string | null }[]): Record<number, string> {
  const out: Record<number, string> = {};
  const seen = new Set<string>();
  /* O3-07a: la direccion guardada del API manda (renombrar ya no la mueve); sin ella, la del nombre como antes */
  [...cats].sort((a, b) => a.id - b.id).forEach((c) => { let s = c.slug || slugify(c.name); if (!c.slug && seen.has(s)) s = `${s}-${c.id}`; seen.add(s); out[c.id] = s; });
  return out;
}
/** O2-03: mientras el cajon del carrito o la hoja de filtros estan abiertos, Tab y Mayus+Tab giran dentro de ellos; al
    abrir, el foco entra al primer control y, al cerrar, vuelve a donde estaba (si ese control sigue en la pagina). */
const FOCOS = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
export function useFocusTrap(ref: { current: HTMLElement | null }, open: boolean) {
  useEffect(() => {
    const box = ref.current;
    if (!open || !box) return;
    const prev = document.activeElement as HTMLElement | null;
    const items = () => [...box.querySelectorAll<HTMLElement>(FOCOS)].filter((el) => el.getClientRects().length > 0);
    (items()[0] || box).focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const list = items();
      if (!list.length) { e.preventDefault(); return; }
      const first = list[0], last = list[list.length - 1], cur = document.activeElement;
      if (e.shiftKey && (cur === first || !box.contains(cur))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (cur === last || !box.contains(cur))) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (prev && prev !== document.body && document.contains(prev)) prev.focus({ preventScroll: true });
    };
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** ruta interna con el prefijo de la tienda */
export const usePath = () => { const { base } = useStore(); return (p: string) => `${base}${p.startsWith("/") ? p : `/${p}`}` || "/"; };

// ---------- iconos (trazo 2px, mismos de la landing) ----------
export const IC = {
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  bag: "M6 7h12l1 14H5L6 7zM9 7a3 3 0 0 1 6 0",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  check: "M5 12.5l4.5 4.5L19 7",
  x: "M6 6l12 12M18 6L6 18",
  back: "M15 18l-6-6 6-6",
  next: "M9 18l6-6-6-6",
  wa: "M3 21l1.6-4.7A8.5 8.5 0 1 1 7.7 19.4L3 21zM9 8.5c0 3.5 3 6.5 6.5 6.5l1-1.6-2-1-1 1c-1.2-.5-2.4-1.7-2.9-2.9l1-1-1-2L9 8.5z",
  shield: "M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6l8-3zM8.5 12l2.5 2.5 4.5-5",
  tool: "M14.7 6.3a4 4 0 0 0 5 5L21 13l-8 8-3-3 8-8-1.3-1.3a4 4 0 0 0-5-5L14 6zM3 21l6-6",
  truck: "M3 6h11v10H3zM14 10h4l3 3v3h-7zM7 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM17 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
  doc: "M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6",
  chat: "M4 5h16v11H8l-4 4z",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 8v5M12 16.5v.5",
  compare: "M4 6h7v14H4zM13 4h7v14h-7z",
  share: "M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8.6 13.5l6.8 4M15.4 6.5l-6.8 4",
  pdf: "M7 3h7l5 5v13H7zM14 3v5h5M9.5 15h1a1.5 1.5 0 0 0 0-3h-1v5M14 12v5h1a2.5 2.5 0 0 0 0-5h-1",
  filter: "M4 5h16M7 12h10M10 19h4",
  zoom: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3M11 8v6M8 11h6",
  cam: "M3 7h4l2-2h6l2 2h4v12H3zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  install: "M12 2v6M12 22v-6M4.9 4.9l4.2 4.2M14.9 14.9l4.2 4.2M2 12h6M22 12h-6",
  // iconografia de categorias y del configurador (mismo trazo 2px)
  cctv: "M3 4h18M5 7h14v2a7 7 0 0 1-14 0zM12 14a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM12 4v3",
  lock: "M6 11h12v10H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3M12 15v2",
  net: "M3 14h18v6H3zM7 17h.01M10.5 17h.01M15 17h3M8.5 10.5a5 5 0 0 1 7 0M5.8 7.8a9 9 0 0 1 12.4 0M12 14v-1",
  bolt: "M13 2L4 14h7l-1 8 9-12h-7z",
  disk: "M4 5h16v14H4zM4 13h16M8 16.5h.01M12 16.5h4",
  people: "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M16 4.5a3.5 3.5 0 0 1 0 6.5M21 20c0-2.6-1.6-4.8-4-5.6",
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  home: "M4 11l8-7 8 7M6 9.5V20h12V9.5M10 20v-5h4v5",
  shop: "M4 9l1.5-5h13L20 9M4 9h16v2a3 3 0 0 1-5.3 1.9A3 3 0 0 1 12 14a3 3 0 0 1-2.7-1.1A3 3 0 0 1 4 11zM5 13.5V20h14v-6.5M10 20v-4h4v4",
  building: "M5 21V4h9v17M14 9h5v12M8 8h3M8 12h3M8 16h3M3 21h18",
  factory: "M3 21V10l5 3V10l5 3V7l8 4v10zM7 17h2M12 17h2M17 17h2",
  phone: "M8 2h8v20H8zM11 18h2",
  appliance: "M6 2h12v20H6zM6 9h12M9 5v1.5M9 12v3",
  speaker: "M4 9v6h4l5 4V5L8 9H4zM16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11",
};

/** icono por nombre de categoria (los nombres los pone Crimson; si no calza ninguno, la camara de la marca) */
const CAT_IC: [RegExp, string][] = [
  [/vigil|c[aá]mara|cctv|video/i, IC.cctv],
  [/control de acceso|seguridad|alarma|cerradura/i, IC.lock], // O1-04 g: «Accesorios» contiene «acceso»; el candado es de control de acceso
  [/red|wifi|switch|cable/i, IC.net],
  [/electrodom|refri|lavad|secador|cocina|horno|microond|licuad|freidor|cafeter|ventilad|aspirad/i, IC.appliance],
  [/smart|hogar|inteligente/i, IC.home],
  [/energ|ups|respaldo|el[eé]ctric/i, IC.bolt],
  [/almacen|disco|memoria/i, IC.disk],
  [/audio|sonido|parlant|bocina|micr[oó]fono/i, IC.speaker],
  [/colabor|reuni|confer/i, IC.people],
  [/herramient|accesorio|instala/i, IC.tool],
];
export const catIcon = (name: string) => CAT_IC.find(([re]) => re.test(name))?.[1] || IC.cam;
export const Ico = ({ d, size = 18 }: { d: string; size?: number }) => (
  <svg className="sh-icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);

// ---------- precio ----------
export function PriceTag({ p, big = false }: { p: Pick<VProd, "display_price" | "display_currency" | "compare_display_price" | "price_on_request">; big?: boolean }) {
  if (p.price_on_request || !Number(p.display_price)) return <span className={`vt-price vt-price--ask${big ? " vt-price--big" : ""}`}>Precio a consultar</span>;
  const cmp = p.compare_display_price && Number(p.compare_display_price) > Number(p.display_price) ? Number(p.compare_display_price) : null;
  return (
    <span className={`vt-price${big ? " vt-price--big" : ""}`}>
      {cmp && <s aria-label={`Antes ${money(cmp, p.display_currency)}`}>{money(cmp, p.display_currency)}</s>}
      <b><Money v={p.display_price} cur={p.display_currency} /></b>
      <small>IVA incluido</small>
    </span>
  );
}

// ---------- disponibilidad: el endpoint de inventario manda; si no existe (404) se usa la del catalogo ----------
let invMissing = false; // no insistir con un endpoint que no existe en esta version del API
const NORM: Record<string, string> = {
  disponible: "disponible", en_bodega: "disponible", in_stock: "disponible", available: "disponible",
  ultimas: "ultimas", ultimas_unidades: "ultimas", low: "ultimas", low_stock: "ultimas",
  bajo_pedido: "bajo_pedido", backorder: "bajo_pedido", pedido: "bajo_pedido",
  agotado: "agotado", out: "agotado", out_of_stock: "agotado", sin_stock: "agotado",
  servicio: "servicio", consultar: "consultar",
};
/* Respaldo si el API no manda la etiqueta (version vieja o mock): el texto lo pone el API (availability._public_label y
   _public_short, O1-02); aca solo se repite por si el campo falta. */
const LABEL: Record<string, string> = { disponible: "Disponible", ultimas: "Pocas unidades en bodega", bajo_pedido: "Bajo pedido", agotado: "Agotado", consultar: "Disponibilidad por confirmar", servicio: "Servicio" };
const SHORT: Record<string, string> = { ...LABEL, ultimas: "Pocas unidades", consultar: "Por confirmar" };
type InvEntry = { estado?: string; state?: string; status?: string; label?: string; label_short?: string | null; accion?: string | null; available?: number; quantity?: number; cantidad?: number | string | null; ultimas?: boolean; disponible?: boolean; max_qty?: number | string | null; blocked?: boolean };
function normInv(e: InvEntry | undefined): Avail | null {
  if (!e) return null;
  // un solo diccionario de estados en el API; aca solo se traduce a la estetica de la vitrina (en_bodega -> disponible/ultimas)
  let s = NORM[String(e.state || e.estado || e.status || "").toLowerCase()];
  if (!s) return null;
  if (s === "disponible" && e.ultimas) s = "ultimas";
  const qty = e.cantidad != null && e.cantidad !== "" ? Number(e.cantidad) : null;
  const max = e.max_qty != null && e.max_qty !== "" ? Number(e.max_qty) : (s === "disponible" || s === "ultimas" ? (e.available ?? e.quantity ?? qty ?? null) : null);
  return { state: s, label: e.label || LABEL[s], label_short: e.label_short || null, accion: e.accion ?? null, max_qty: max ?? null, blocked: e.disponible === false || !!e.blocked || s === "agotado" || s === "consultar", cantidad: qty, ultimas: !!e.ultimas, disponible: e.disponible };
}
/** la disponibilidad que trae una tarjeta del catalogo (o la de /availability ya normalizada) en la forma de la vitrina */
export const normAvail = (a?: Avail | null): Avail | null => (a ? normInv(a as unknown as InvEntry) || a : null);
/** Disponibilidad en vivo por id. Acepta {items:[...]}, [...] o {"<id>": {...}} para no atarse al formato del otro modulo. */
export function useAvailability(ids: number[]): Record<number, Avail> {
  const { slug } = useStore();
  const [map, setMap] = useState<Record<number, Avail>>({});
  const key = useMemo(() => [...new Set(ids)].sort((a, b) => a - b).join(","), [ids]);
  useEffect(() => {
    if (!key || invMissing) return;
    let alive = true;
    fetch(`/api/public/store/${slug}/availability?ids=${key}`)
      .then((r) => { if (r.status === 404 || r.status === 405) { invMissing = true; return null; } return r.ok ? r.json() : null; })
      .then((j) => {
        if (!alive || !j) return;
        const out: Record<number, Avail> = {};
        const list: (InvEntry & { id?: number; product_id?: number })[] = Array.isArray(j) ? j : Array.isArray(j.items) ? j.items : Object.entries(j).map(([k, v]) => ({ ...(v as InvEntry), id: Number(k) }));
        for (const e of list) { const id = Number(e.product_id ?? e.id); const a = normInv(e); if (id && a) out[id] = a; }
        setMap(out);
      })
      .catch(() => { /* sin inventario en vivo: queda la del catalogo */ });
    return () => { alive = false; };
  }, [key, slug]);
  return map;
}
export function AvailBadge({ a: raw, compact = false }: { a?: Avail | null; compact?: boolean }) {
  const a = normAvail(raw);
  if (!a || a.state === "servicio") return null;
  return <span className="vt-avail" data-s={a.state}>{compact ? shortLabel(a) : a.label}</span>;
}
/** etiqueta de la tarjeta: la del API; si falta, «{N} en bodega» con pocas unidades y cantidad publicada, o el respaldo */
function shortLabel(a: Avail): string {
  if (a.label_short) return a.label_short;
  if (a.state === "ultimas" && a.cantidad != null && a.cantidad > 0) return `${a.cantidad} en bodega`;
  return SHORT[a.state] || a.label;
}
/** que ofrece la tienda cuando no se compra en linea (O1-02): whatsapp, o nada. «avisarme» se porta como whatsapp hasta
    que exista el aviso (O2-10). Sin numero de WhatsApp en la tienda no hay boton que ofrecer. */
export const assistAction = (a: Avail | null | undefined, whatsapp?: string | null): "whatsapp" | "ninguna" => {
  if (!whatsapp) return "ninguna";
  return a?.accion === "ninguna" ? "ninguna" : "whatsapp";
};
/** no se puede comprar en linea: agotado, dato del proveedor viejo ("consultar") o el API dice que no es vendible */
export const cannotBuy = (raw?: Avail | null) => {
  const a = normAvail(raw);
  return !!a && (a.state === "agotado" || a.state === "consultar" || !!a.blocked);
};

// ---------- marco de visor (esquinas tipo camara, como la landing) ----------
export function Viewfinder({ children, label, right, live = false, className = "" }: { children: ReactNode; label?: string; right?: string; live?: boolean; className?: string }) {
  return (
    <div className={`vt-vf ${className}`}>
      {children}
      <i className="vt-vf__corners" aria-hidden="true" />
      {(label || right) && (
        <span className="vt-vf__hud" aria-hidden="true">
          <span>{live && <i className="vt-rec" />}{label}</span>
          {right && <span>{right}</span>}
        </span>
      )}
    </div>
  );
}

// ---------- tarjeta de producto (O2-06: seis elementos y cero adornos) ----------
const NUEVO_DIAS = 30;
/** una sola insignia y solo con dato real: «Nuevo» (alta de 30 dias o menos); la fila «Listos para entrega» pasa la suya */
function cardBadge(p: VProd): string | null {
  if (p.created_ts && Date.now() / 1000 - p.created_ts <= NUEVO_DIAS * 86_400) return "Nuevo";
  return null;
}
/** 1 foto y nombre en un solo enlace · 2 marca · 3 nombre · 4 datos clave · 5 precio, IVA y disponibilidad · 6 accion */
export function ProductCard({ p, avail, eager = false, badge }: { p: VProd; avail?: Avail; n?: number; eager?: boolean; badge?: string | null }) {
  p = conNombreVisible(p); // O3-01: nombre comercial y codigo publico (con respaldo a los de siempre)
  const ctx = useStore();
  const to = usePath();
  const [done, setDone] = useState(false);
  const t = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(t.current), []);
  const a = avail || normAvail(p.availability);
  const ask = p.price_on_request || !Number(p.display_price);
  const noBuy = ctx.isShop && cannotBuy(a); // en la tienda: lo que no se puede comprar en linea se consulta por WhatsApp
  const inCompare = ctx.compare.includes(p.id);
  const tag = badge === undefined ? cardBadge(p) : badge;
  const ks = (p.ks || []).filter(Boolean);
  const add = () => {
    if (p.variants?.length) return; // con opciones hay que elegir en la ficha (el enlace lo resuelve)
    ctx.addToCart(p, 1);
    setDone(true);
    window.clearTimeout(t.current);
    t.current = window.setTimeout(() => setDone(false), 1400);
  };
  return (
    <article className="vt-card">
      {/* un solo enlace para foto y nombre: la foto va sin texto alternativo y el lector dice el nombre una vez */}
      <Link to={to(p.path)} className="vt-card__link">
        <span className="vt-card__media">
          {p.image ? <img src={p.image} alt="" loading={eager ? "eager" : "lazy"} decoding="async" width={400} height={400} sizes="(max-width: 767px) 50vw, 280px" /> : <span className="sh-noimg">Sin foto</span>}
          {tag && <span className="vt-card__badge">{tag}</span>}
        </span>
        {p.brand && <span className="vt-card__meta">{p.brand}</span>}
        <span className="vt-card__name">{p.name}</span>
      </Link>
      {ks.length > 0 && <p className="vt-card__ks">{ks.map((k, i) => <span key={k} className={i ? "vt-card__ks-more" : undefined}>{i ? ` · ${k}` : k}</span>)}</p>}
      <div className="vt-card__foot">
        <div className="vt-card__buy">
          <PriceTag p={p} />
          <AvailBadge a={a} compact />
        </div>
        {ask ? (
          <Link className="vt-add vt-add--pill" to={to(p.path)} aria-label={`Consultar precio de ${p.name}`}>Consultar</Link>
        ) : noBuy ? (
          assistAction(a, ctx.store.whatsapp) === "whatsapp"
            ? <a className="vt-add vt-add--pill" href={waLink(ctx.store.whatsapp, a?.state === "agotado" ? `Hola, ¿cuándo vuelven a tener ${p.name}${p.code ? ` (código ${p.code})` : ""}?` : mensajePedido({ id: p.id, nombre: p.name, codigo: p.code, cantidad: 1, precio: p.display_price, moneda: p.display_currency, url: absUrl(to(p.path)) }))} target="_blank" rel="noopener noreferrer" aria-label={`Consultar ${p.name} por WhatsApp`}><Ico d={IC.wa} size={16} />Consultar</a>
            : <Link className="vt-add vt-add--pill" to={to(p.path)} aria-label={`Ver ${p.name}`}>Ver</Link>
        ) : p.variants?.length ? (
          <Link className="vt-add" to={to(p.path)} aria-label={`Ver opciones de ${p.name}`}><Ico d={IC.next} /></Link>
        ) : (
          <button type="button" className="vt-add" data-done={done} onClick={add} aria-label={`Agregar ${p.name} ${ctx.isShop ? "al carrito" : "a la cotización"}`}>
            <Ico d={done ? IC.check : IC.plus} />
          </button>
        )}
      </div>
      {/* comparar: solo en escritorio, al pasar el puntero o con el foco del teclado (en celular queda en la ficha) */}
      <label className="vt-cmp">
        <input type="checkbox" checked={inCompare} onChange={() => ctx.toggleCompare(p.id)} disabled={!inCompare && ctx.compare.length >= 3} />
        <span>Comparar</span>
      </label>
    </article>
  );
}

/** rail: en movil y tableta la fila se desliza de lado (scroll nativo con snap, cero JS); en escritorio es grilla de 4 */
export function Grid({ items, avail, eager = 0, numbered = false, rail = false }: { items: VProd[]; avail?: Record<number, Avail>; eager?: number; numbered?: boolean; rail?: boolean }) {
  return (
    <ul className={`vt-grid${rail ? " vt-grid--rail" : ""}`}>
      {items.map((p, i) => <li key={p.id}><ProductCard p={p} avail={avail?.[p.id]} eager={i < eager} n={numbered ? i + 1 : undefined} /></li>)}
    </ul>
  );
}

/** esqueleto con la misma geometria que la tarjeta real: cuando llegan los productos nada salta */
export function Skeleton({ n = 8, rail = false }: { n?: number; rail?: boolean }) {
  return (
    <ul className={`vt-grid${rail ? " vt-grid--rail" : ""}`} aria-hidden="true">
      {Array.from({ length: n }, (_, i) => (
        <li key={i}>
          <div className="vt-card vt-card--skel">
            {/* O2-14: las mismas piezas que la tarjeta real, con texto invisible: al llegar los productos nada salta */}
            <span className="vt-card__link"><span className="vt-card__media sh-skel" /><span className="vt-card__meta">&nbsp;</span><span className="vt-card__name">&nbsp;</span></span>
            <p className="vt-card__ks">&nbsp;</p>
            <div className="vt-card__foot"><div className="vt-card__buy"><span className="vt-price"><b>&nbsp;</b><small>&nbsp;</small></span><span className="vt-avail">&nbsp;</span></div><span className="vt-add sh-skel" /></div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function Crumbs({ items }: { items: { name: string; path?: string }[] }) {
  const to = usePath();
  return (
    <nav className="vt-crumbs" aria-label="Ruta">
      <ol>
        {items.map((c, i) => (
          <li key={i}>{c.path && i < items.length - 1 ? <Link to={to(c.path)}>{c.name}</Link> : <span aria-current={i === items.length - 1 ? "page" : undefined}>{c.name}</span>}</li>
        ))}
      </ol>
    </nav>
  );
}

// ---------- SEO del lado del navegador: actualiza las mismas etiquetas que nginx incrusta (data-sh) ----------
/** Bloque seo que devuelven /item, /catalog, /showcase e /info (O1-06): lo mismo que nginx incrusta por SSI para esa
    ruta, armado por el API (seo_meta). Aditivo: un API viejo no lo manda y la vitrina usa lo suyo. */
export type SeoBlock = { title: string; description?: string; canonical?: string | null; robots?: string; og_type?: string; og_image?: string | null; og_image_alt?: string | null; ld?: object | null };
export type HeadMeta = { title: string; description?: string; path?: string; image?: string | null; type?: string; noindex?: boolean; seo?: SeoBlock | null };
/* O1-06: la ruta con la que se abrio la pagina, si el servidor ya puso sus metas (nginx SSI: etiquetas data-sh).
   En esa ruta el navegador no toca nada: el titulo, la descripcion, la canonica, robots, Open Graph y el JSON-LD que
   ve Google son los del servidor, sin un segundo JSON-LD ni un «index,follow» encima de un noindex. Al navegar a otra
   ruta, se olvida y el navegador pone el bloque seo del API (o lo suyo si no viene). */
let ssrPath: string | null = typeof document !== "undefined" && document.head.querySelector("[data-sh]") ? location.pathname : null;
/** solo para las pruebas: simula una carga con (o sin) metas del servidor */
export const _setSsrPath = (p: string | null) => { ssrPath = p; };
export function serverOwnsHead(pathname: string): boolean {
  if (ssrPath === null) return false;
  if (pathname === ssrPath) return true;
  ssrPath = null; // primera navegacion interna: desde aqui manda el navegador
  return false;
}
export function useHead(m: HeadMeta | null) {
  const { hostMode, store } = useStore();
  const json = JSON.stringify(m);
  useEffect(() => {
    if (!m) return;
    document.documentElement.lang = "es-CR";
    if (serverOwnsHead(location.pathname)) return;
    const seo = hostMode ? m.seo || null : null;
    document.title = seo?.title || m.title;
    const canonical = seo ? seo.canonical || null : hostMode && m.path !== undefined ? `${location.origin}${m.path}` : null;
    const set = (attr: "name" | "property", key: string, val: string | null | undefined) => {
      let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`);
      if (!val) { el?.remove(); return; }
      if (!el) { el = document.createElement("meta"); el.setAttribute(attr, key); el.setAttribute("data-sh", ""); document.head.appendChild(el); }
      el.content = val;
    };
    const desc = seo ? seo.description || "" : m.description || store.tagline || `Tienda en línea de ${store.name}`;
    set("name", "description", desc);
    // robots: el del API; sin bloque, nada (lo normal es indexar) salvo noindex. Fuera del dominio propio, nunca se indexa.
    set("name", "robots", !hostMode ? "noindex" : m.noindex ? "noindex,follow" : seo?.robots || null);
    set("property", "og:title", seo?.title || m.title);
    set("property", "og:description", desc);
    set("property", "og:type", seo?.og_type || m.type || "website");
    set("property", "og:image", seo ? seo.og_image || null : m.image || null);
    set("property", "og:image:alt", seo?.og_image ? seo.og_image_alt || seo.title : null);
    set("property", "og:url", canonical);
    // O2-01c: las vecinas de un listado paginado (rel prev/next) son del servidor y solo valen en la ruta con que se abrio
    document.head.querySelectorAll('link[rel="prev"][data-sh], link[rel="next"][data-sh]').forEach((el) => el.remove());
    let link = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
    if (canonical) { if (!link) { link = document.createElement("link"); link.rel = "canonical"; link.setAttribute("data-sh", ""); document.head.appendChild(link); } link.href = canonical; } else link?.remove();
    // un solo JSON-LD, el del servidor: sin bloque seo no se arma uno propio
    let s = document.getElementById("sh-ld");
    if (seo?.ld) {
      if (!s) { s = document.createElement("script"); s.id = "sh-ld"; s.setAttribute("type", "application/ld+json"); s.setAttribute("data-sh", ""); document.head.appendChild(s); }
      s.textContent = JSON.stringify(seo.ld).replace(/</g, "\\u003c");
    } else s?.remove();
  }, [json, hostMode, store.name, store.tagline]); // eslint-disable-line react-hooks/exhaustive-deps
}

export function EmptyHelp({ title, text, children }: { title: string; text?: string; children?: ReactNode }) {
  const { store } = useStore();
  const to = usePath();
  return (
    <div className="vt-empty">
      <Ico d={IC.cam} size={28} />
      <b>{title}</b>
      {text && <span>{text}</span>}
      <div className="vt-empty__actions">
        {children}
        <Link className="sh-btn sh-btn--ghost" to={to("/asesoria")}>Pedir asesoría</Link>
        {store.whatsapp && <a className="sh-btn sh-btn--wa" href={waLink(store.whatsapp, `Hola ${store.name}, estoy buscando un producto y no lo encuentro en la tienda.`)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />WhatsApp</a>}
      </div>
    </div>
  );
}

/** Foto fija de un equipo del kit (la arma el API: los equipos reales del kit o los que eligio Andres en Vitrina). */
export type KitPhoto = { image: string; label: string; name: string };

/** Fotos fijas de los equipos de un kit (pedido 21/10): 1 a 4 en mosaico, cada una con su rotulo tipo visor. Nada se
    mueve. Sin fotos (catalogo sin imagenes) cae al muro de camaras, que dice cuantas trae. */
export function KitPhotos({ photos, label, cameras = 0, eager = false }: { photos: KitPhoto[]; label?: string; cameras?: number; eager?: boolean }) {
  const shown = photos.slice(0, 4);
  if (!shown.length) {
    const n = Math.max(1, Math.min(16, cameras || 1));
    const cols = Math.ceil(Math.sqrt(n));
    return (
      <span className="vt-kit__img vt-kit__wall" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }} aria-hidden="true">
        {Array.from({ length: cols * cols }, (_, j) => (
          <span key={j} className="vt-kit__cell" data-on={j < n}>{j < n && <Ico d={IC.cam} size={cols > 3 ? 12 : 16} />}{j < n && <i>{j === 0 && label ? label : `CAM ${String(j + 1).padStart(2, "0")}`}</i>}</span>
        ))}
      </span>
    );
  }
  return (
    <span className="vt-kit__img vt-kit__photos" data-n={shown.length}>
      {shown.map((ph, j) => (
        <span key={ph.image + j} className="vt-kit__photo">
          <img src={ph.image} alt={ph.name} loading={eager ? "eager" : "lazy"} decoding="async" width={240} height={180} />
          <i>{j === 0 && label ? <><b className="vt-rec" />{label} · </> : null}{ph.label}</i>
        </span>
      ))}
    </span>
  );
}

/** Texto largo de la tienda (terminos, privacidad, envios): parrafos separados por linea en blanco; una primera linea
    corta sin punto final se lee como subtitulo. Lo usan las paginas de informacion y el modal del checkout. */
export function Prose({ text }: { text: string }) {
  const blocks = text.replace(/\r/g, "").split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
  return (
    <div className="vt-prose">
      {blocks.map((b, i) => {
        const [first, ...rest] = b.split("\n");
        const heading = rest.length > 0 && first.length < 60 && !/[.:;,]$/.test(first);
        return heading ? <section key={i}><h2>{first}</h2><p>{rest.join("\n")}</p></section> : <p key={i}>{b}</p>;
      })}
    </div>
  );
}

// ---------- tema (O4-06) ----------
/* Tema de la tienda (O4-06): lo que el administrador elige en Mi Tienda -> Apariencia, como variables CSS del
   contenedor .shop (los tokens de shop.css, Ola 2: --crimson, --crimson-press, --r-1, --r-2, --paper, --paper-2,
   --font). Modulo puro: lo usan la vista previa del panel y la tienda publica (PublicStore), y se prueba en Vitest
   (__tests__/tema.test.ts).

   Solo se emite lo que DIFIERE de la tienda de hoy: con el tema por defecto (o sin tema, API viejo) themeVars devuelve
   {} y la tienda queda exactamente igual (las capturas base no cambian). */

export type Theme = {
  /** clasico = Arial (el de hoy); crimson = Archivo, solo si el dueno lo aprueba (O2-04) */
  preset?: "clasico" | "crimson";
  accent?: string;
  /** radio de campos y fotos, en px (0 a 16); el de tarjetas y paneles va en proporcion (8 -> 14, el de hoy) */
  radius?: number;
  /** fondo de pagina: crema (el de hoy) o blanco */
  background?: "crema" | "blanco";
};

export const THEME_DEFAULT: Required<Theme> = { preset: "clasico", accent: "#e2233a", radius: 8, background: "crema" };
const HEX = /^#[0-9a-fA-F]{6}$/;
const FONDOS = { crema: { bg: "#f7f5f2", bg2: "#efebe6" }, blanco: { bg: "#ffffff", bg2: "#f4f4f5" } };

function rgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const lin = (c: number) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
export function luminance(hex: string): number {
  const [r, g, b] = rgb(hex);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
/** contraste WCAG entre dos colores (1 a 21) */
export function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m);
  return Math.round(((x + 0.05) / (y + 0.05)) * 100) / 100;
}
/** el color de acento lleva texto blanco (botones): AA pide 4,5 */
export const accentOk = (accent: string) => HEX.test(accent) && contrast(accent, "#ffffff") >= 4.5;

/** un tono mas oscuro (hover) */
export function darker(hex: string, f = 0.86): string {
  const [r, g, b] = rgb(hex).map((c) => Math.max(0, Math.round(c * f)));
  return `#${[r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

/** variables CSS del tema para el contenedor .shop; {} con el tema por defecto o sin tema (la tienda queda como hoy) */
export function themeVars(t?: Theme | null): Record<string, string> {
  if (!t) return {};
  const out: Record<string, string> = {};
  if (t.accent && HEX.test(t.accent) && t.accent.toLowerCase() !== THEME_DEFAULT.accent) {
    out["--crimson"] = t.accent;
    out["--crimson-press"] = darker(t.accent);
  }
  if (typeof t.radius === "number" && t.radius >= 0 && t.radius <= 16 && t.radius !== THEME_DEFAULT.radius) {
    out["--r-1"] = `${t.radius}px`;
    out["--r-2"] = `${Math.round((t.radius * 14) / 8)}px`;
  }
  if (t.background === "blanco") { out["--paper"] = FONDOS.blanco.bg; out["--paper-2"] = FONDOS.blanco.bg2; }
  if (t.preset === "crimson") out["--font"] = "Archivo, Arial, Helvetica, sans-serif";
  return out;
}
