/* Piezas compartidas de la vitrina: tipos, contexto de la tienda, precios, disponibilidad, tarjeta de producto,
   marco de visor (estetica "centro de monitoreo" de crimsoncr.com) y metas SEO del lado del navegador.
   El carrito NO vive aqui: la vitrina solo llama a ctx.addToCart (lo provee PublicStore / el modulo de pedidos). */
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

/** blocked: el inventario dice que no se puede vender todavia (disponible === false), aunque el estado no sea "agotado" */
export type Avail = { state: string; label: string; max_qty?: number | null; blocked?: boolean };
export type Variant = { id: number; name: string; price: string | number; display_price?: string | number };
export type VProd = {
  id: number; code: string; name: string; brand: string | null; model?: string | null; image: string | null; images: string[];
  description: string | null; category: string | null; category_id: number | null; category_path?: string | null; path: string; type?: string;
  tax_rate: number; item_type: string; display_price: string | number; display_currency: string; compare_display_price?: string | number | null;
  price_on_request?: boolean; availability: Avail; variants: Variant[]; price: string | number; currency: string;
};
export type HomeStore = { name: string; tagline: string; logo_url: string | null; primary: string; secondary: string; font: string; kind: string; whatsapp: string; currency: string; legal: { privacy: string; terms: string } };

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
};
const Ctx = createContext<StoreCtx | null>(null);
export const StoreProvider = ({ value, children }: { value: StoreCtx; children: ReactNode }) => <Ctx.Provider value={value}>{children}</Ctx.Provider>;
export function useStore(): StoreCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useStore fuera de la tienda");
  return c;
}

export const money = (v: string | number, cur = "CRC") =>
  new Intl.NumberFormat("es-CR", { style: "currency", currency: cur, currencyDisplay: "narrowSymbol", minimumFractionDigits: cur === "CRC" ? 0 : 2, maximumFractionDigits: cur === "CRC" ? 0 : 2 }).format(Number(v) || 0);
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
export function catSlugs(cats: { id: number; name: string }[]): Record<number, string> {
  const out: Record<number, string> = {};
  const seen = new Set<string>();
  [...cats].sort((a, b) => a.id - b.id).forEach((c) => { let s = slugify(c.name); if (seen.has(s)) s = `${s}-${c.id}`; seen.add(s); out[c.id] = s; });
  return out;
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
};

/** icono por nombre de categoria (los nombres los pone Crimson; si no calza ninguno, la camara de la marca) */
const CAT_IC: [RegExp, string][] = [
  [/vigil|c[aá]mara|cctv|video/i, IC.cctv],
  [/acceso|seguridad|alarma|cerradura/i, IC.lock],
  [/red|wifi|switch|cable/i, IC.net],
  [/energ|ups|respaldo|el[eé]ctric/i, IC.bolt],
  [/almacen|disco|memoria/i, IC.disk],
  [/colabor|audio|reuni|confer/i, IC.people],
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
      <b>{money(p.display_price, p.display_currency)}</b>
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
const LABEL: Record<string, string> = { disponible: "Disponible", ultimas: "Últimas unidades", bajo_pedido: "Bajo pedido", agotado: "Agotado", consultar: "Consultar disponibilidad", servicio: "Servicio" };
const SHORT: Record<string, string> = { ...LABEL, consultar: "Consultar stock" };
type InvEntry = { estado?: string; state?: string; status?: string; label?: string; available?: number; quantity?: number; cantidad?: number | string | null; ultimas?: boolean; disponible?: boolean; max_qty?: number | null };
function normInv(e: InvEntry | undefined): Avail | null {
  if (!e) return null;
  // "estado" es el dato real del inventario; "state" es el nombre viejo (consultar -> agotado) que se conserva por compatibilidad
  let s = NORM[String(e.estado || e.state || e.status || "").toLowerCase()];
  if (!s) return null;
  if (s === "disponible" && e.ultimas) s = "ultimas";
  const qty = e.cantidad != null && e.cantidad !== "" ? Number(e.cantidad) : null;
  const max = e.max_qty ?? (s === "disponible" || s === "ultimas" ? (e.available ?? e.quantity ?? qty ?? null) : null);
  return { state: s, label: e.label || LABEL[s], max_qty: max ?? null, blocked: e.disponible === false };
}
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
export function AvailBadge({ a, compact = false }: { a?: Avail | null; compact?: boolean }) {
  if (!a || a.state === "servicio") return null;
  return <span className="vt-avail" data-s={a.state}>{compact ? SHORT[a.state] || a.label : a.label}</span>;
}
/** no se puede agregar: agotado, o el inventario dice que todavia no es vendible (antes ambos llegaban como "agotado") */
export const cannotBuy = (a?: Avail | null) => !!a && (a.state === "agotado" || (a.state === "consultar" && !!a.blocked));

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

// ---------- tarjeta de producto ----------
export function ProductCard({ p, avail, n, eager = false }: { p: VProd; avail?: Avail; n?: number; eager?: boolean }) {
  const ctx = useStore();
  const to = usePath();
  const [done, setDone] = useState(false);
  const t = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(t.current), []);
  const a = avail || p.availability;
  const ask = p.price_on_request || !Number(p.display_price);
  const inCompare = ctx.compare.includes(p.id);
  const add = () => {
    if (p.variants?.length) return; // con opciones hay que elegir en la ficha (el enlace lo resuelve)
    ctx.addToCart(p, 1);
    setDone(true);
    window.clearTimeout(t.current);
    t.current = window.setTimeout(() => setDone(false), 1400);
  };
  return (
    <article className="vt-card">
      <Link to={to(p.path)} className="vt-card__media" aria-label={p.name} tabIndex={-1}>
        <Viewfinder label={n ? `CAM ${String(n).padStart(2, "0")}` : undefined}>
          {p.image ? <img src={p.image} alt="" loading={eager ? "eager" : "lazy"} decoding="async" width={400} height={400} sizes="(max-width: 520px) 50vw, 260px" /> : <span className="sh-noimg">Sin foto</span>}
        </Viewfinder>
      </Link>
      <div className="vt-card__body">
        <span className="sh-meta vt-card__meta">{[p.brand, p.type || p.category].filter(Boolean).join(" · ") || " "}</span>
        <Link to={to(p.path)} className="vt-card__name">{p.name}</Link>
        <AvailBadge a={a} compact />
        <div className="vt-card__foot">
          <PriceTag p={p} />
          {ask ? (
            <Link className="vt-add vt-add--ask" to={to(p.path)} aria-label={`Consultar precio de ${p.name}`}><Ico d={IC.chat} /></Link>
          ) : p.variants?.length ? (
            <Link className="vt-add" to={to(p.path)} aria-label={`Ver opciones de ${p.name}`}><Ico d={IC.next} /></Link>
          ) : (
            <button className="vt-add" data-done={done} onClick={add} aria-label={`${ctx.isShop ? "Agregar al carrito" : "Agregar a la cotización"}: ${p.name}`}>
              <Ico d={done ? IC.check : IC.plus} />
            </button>
          )}
        </div>
        <label className="vt-cmp">
          <input type="checkbox" checked={inCompare} onChange={() => ctx.toggleCompare(p.id)} disabled={!inCompare && ctx.compare.length >= 3} />
          <span>Comparar</span>
        </label>
      </div>
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
            <div className="vt-card__media"><div className="vt-vf sh-skel" /></div>
            <div className="vt-card__body">
              <span className="sh-skel" style={{ height: 10, width: "45%" }} />
              <span className="sh-skel" style={{ height: 14, width: "92%" }} />
              <span className="sh-skel" style={{ height: 14, width: "70%" }} />
              <span className="sh-skel" style={{ height: 12, width: "38%" }} />
              <div className="vt-card__foot"><span className="sh-skel" style={{ height: 22, width: "48%" }} /><span className="sh-skel" style={{ height: 44, width: 44, borderRadius: "50%" }} /></div>
              <span className="sh-skel" style={{ height: 16, width: "34%", margin: "8px 0" }} />
            </div>
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
export type HeadMeta = { title: string; description?: string; path?: string; image?: string | null; type?: string; noindex?: boolean; ld?: object | null };
export function useHead(m: HeadMeta | null) {
  const { hostMode, store } = useStore();
  const json = JSON.stringify(m);
  useEffect(() => {
    if (!m) return;
    document.title = m.title;
    document.documentElement.lang = "es-CR";
    const canonical = hostMode && m.path !== undefined ? `${location.origin}${m.path}` : null;
    const set = (attr: "name" | "property", key: string, val: string | null | undefined) => {
      let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`);
      if (!val) { el?.remove(); return; }
      if (!el) { el = document.createElement("meta"); el.setAttribute(attr, key); el.setAttribute("data-sh", ""); document.head.appendChild(el); }
      el.content = val;
    };
    set("name", "description", m.description || store.tagline || `Tienda en línea de ${store.name}`);
    set("name", "robots", hostMode && !m.noindex ? "index,follow" : "noindex");
    set("property", "og:title", m.title);
    set("property", "og:description", m.description || "");
    set("property", "og:type", m.type || "website");
    set("property", "og:image", m.image || null);
    set("property", "og:url", canonical);
    let link = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
    if (canonical) { if (!link) { link = document.createElement("link"); link.rel = "canonical"; link.setAttribute("data-sh", ""); document.head.appendChild(link); } link.href = canonical; } else link?.remove();
    let s = document.getElementById("sh-ld");
    if (m.ld && hostMode) {
      if (!s) { s = document.createElement("script"); s.id = "sh-ld"; s.setAttribute("type", "application/ld+json"); s.setAttribute("data-sh", ""); document.head.appendChild(s); }
      s.textContent = JSON.stringify(m.ld).replace(/</g, "\\u003c");
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
