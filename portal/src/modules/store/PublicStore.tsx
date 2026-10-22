/* Vitrina publica: enrutador delgado. Vive en /tienda/:slug/* del portal y, con dominio propio
   (tienda.crimsoncr.com), en la raiz: ver StoreHost.tsx.

   Direcciones amigables: /  /catalogo  /c/<categoria>  /p/<nombre>-<id>  /buscar?q=  /arma-tu-sistema  /comparar
   /asesoria  /info/<terminos|privacidad|envios|garantias|preguntas-frecuentes>  /pg/<pagina de bloques>.
   Las de la version anterior (?p= ?c= ?q= ?legal= ?pg=) se redirigen a las nuevas.

   Esta pieza solo arma la cabecera, el pie y las rutas. La vitrina vive en StoreHome / CategoryPage / ProductPage /
   KitBuilder / StorePages; el carrito y el checkout (cajon, ?v=pagar|cotizar) siguen aqui abajo sin cambios y son
   del modulo de pedidos. Precios: los entrega el API ya en la moneda de la tienda y CON IVA (display_price). */
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { type PubProduct } from "./Blocks";
import CategoryPage from "./CategoryPage";
import Checkout from "./Checkout";
import OrderStatus, { OrderLookup } from "./OrderStatus";
import Consent from "./Consent";
import { track } from "./analytics";
import ProductPage from "./ProductPage";
import StoreHome from "./StoreHome";
import { catSlugs, normAvail, safeStore, StoreProvider, type StoreCtx, type VProd, type Variant as VVariant } from "./storeKit";
import "./shop.css";
import "./vitrina.css";

const KitBuilder = lazy(() => import("./KitBuilder"));
const AdvisoryPage = lazy(() => import("./StorePages").then((m) => ({ default: m.AdvisoryPage })));
const InfoPage = lazy(() => import("./StorePages").then((m) => ({ default: m.InfoPage })));
const ComparePage = lazy(() => import("./StorePages").then((m) => ({ default: m.ComparePage })));
const BlockPage = lazy(() => import("./StorePages").then((m) => ({ default: m.BlockPage })));
const NotFound = lazy(() => import("./StorePages").then((m) => ({ default: m.NotFound })));

type Cat = { id: number; name: string; count: number };
type Home = {
  store: { name: string; tagline: string; logo_url: string | null; primary: string; secondary: string; font: string; kind: string; whatsapp: string; currency: string; legal: { privacy: string; terms: string } };
  nav: { slug: string; title: string }[];
  categories: Cat[];
  product_count?: number;
  shipping_rates: { name: string; amount: number; per_kg?: number; display_amount?: number }[];
  payment_methods: { name: string; instructions: string }[];
};
type Avail = { state: string; label: string };
type Variant = { id: number; name: string; price: string | number; display_price?: string | number };
type Prod = Omit<PubProduct, "variants"> & { code: string; category: string | null; category_id: number | null; brand: string | null; tax_rate: number; item_type: string; display_price: string | number; display_currency: string; availability: Avail; variants: Variant[] };
/* max: tope en linea del producto (max_qty de la disponibilidad); el checkout lo vuelve a validar */
type Line = { key: string; id: number; variant_id: number | null; name: string; image: string | null; price: number; currency: string; qty: number; max?: number | null };

const money = (v: string | number, cur = "CRC") =>
  new Intl.NumberFormat("es-CR", { style: "currency", currency: cur, currencyDisplay: "narrowSymbol", minimumFractionDigits: cur === "CRC" ? 0 : 2, maximumFractionDigits: cur === "CRC" ? 0 : 2 }).format(Number(v) || 0);
const wa = (phone: string, text: string) => `https://wa.me/${phone.replace(/\D/g, "")}?text=${encodeURIComponent(text)}`;
const store = {
  get(k: string) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k: string, v: string) { try { localStorage.setItem(k, v); } catch { /* modo privado: el carrito vive solo en memoria */ } },
};
const calm = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

const Ico = ({ d, size = 18 }: { d: string; size?: number }) => (
  <svg className="sh-icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);
const IC = {
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  bag: "M6 7h12l1 14H5L6 7zM9 7a3 3 0 0 1 6 0",
  plus: "M12 5v14M5 12h14",
  check: "M5 12.5l4.5 4.5L19 7",
  x: "M6 6l12 12M18 6L6 18",
  back: "M15 18l-6-6 6-6",
  wa: "M3 21l1.6-4.7A8.5 8.5 0 1 1 7.7 19.4L3 21zM9 8.5c0 3.5 3 6.5 6.5 6.5l1-1.6-2-1-1 1c-1.2-.5-2.4-1.7-2.9-2.9l1-1-1-2L9 8.5z",
  compare: "M4 6h7v14H4zM13 4h7v14h-7z",
  cam: "M3 7h4l2-2h6l2 2h4v12H3zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
};

const Spin = () => <div className="vt-spin"><span className="spinner" /></div>;

export default function PublicStore({ slug: slugProp, hostMode = false }: { slug?: string; hostMode?: boolean }) {
  const params = useParams();
  const slug = slugProp || params.slug || "";
  const base = hostMode ? "" : `/tienda/${slug}`;
  const [sp, setSp] = useSearchParams();
  const navigate = useNavigate();
  const loc = useLocation();
  const v = sp.get("v");
  const pedido = sp.get("pedido"); // seguimiento del pedido (enlace firmado del correo)
  const rel = loc.pathname.slice(base.length) || "/";

  const api = useCallback(async <T,>(path: string, init?: RequestInit): Promise<{ data: T; total: number | null }> => {
    const r = await fetch(`/api/public/store/${slug}${path}`, init);
    if (!r.ok) { let m = "No se pudo cargar"; try { m = (await r.json()).detail || m; } catch { /* sin cuerpo */ } throw new Error(typeof m === "string" ? m : "No se pudo cargar"); }
    const t = r.headers.get("X-Total-Count");
    return { data: (await r.json()) as T, total: t ? Number(t) : null };
  }, [slug]);

  const go = useCallback((next: Record<string, string | number | null | undefined>, opts?: { keep?: boolean; replace?: boolean }) => {
    const n = new URLSearchParams(opts?.keep ? sp : undefined);
    for (const [k, val] of Object.entries(next)) { if (val === null || val === undefined || val === "") n.delete(k); else n.set(k, String(val)); }
    setSp(n, { replace: opts?.replace });
    if (!opts?.replace) window.scrollTo({ top: 0, behavior: calm() ? "auto" : "smooth" });
  }, [sp, setSp]);

  // ---------- datos de la tienda (nombre, menu, envios y pagos para el checkout) ----------
  const [home, setHome] = useState<Home | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  useEffect(() => { api<Home>("").then(({ data }) => setHome(data)).catch((e) => setFatal(e.message)); }, [api]);

  // ---------- direcciones de la version anterior (?p= ?c= ?q= ?legal= ?pg=) -> nuevas ----------
  useEffect(() => {
    if (rel !== "/" || v) return;
    const p = sp.get("p"), c = sp.get("c"), q = sp.get("q"), legal = sp.get("legal"), pg = sp.get("pg");
    const to = p ? `/p/producto-${encodeURIComponent(p)}` : c ? `/c/${encodeURIComponent(c)}` : q ? `/buscar?q=${encodeURIComponent(q)}` : legal ? `/info/${legal === "terms" ? "terminos" : "privacidad"}` : pg ? `/pg/${encodeURIComponent(pg)}` : null;
    if (to) navigate(`${base}${to}`, { replace: true });
  }, [rel, v]); // eslint-disable-line react-hooks/exhaustive-deps
  // cada vista nueva arranca arriba (salvo atras/adelante, que el navegador restaura)
  const firstNav = useRef(true);
  useEffect(() => {
    if (firstNav.current) { firstNav.current = false; return; }
    window.scrollTo({ top: 0 });
    document.getElementById("contenido")?.focus({ preventScroll: true });
  }, [loc.pathname]);

  // ---------- carrito (persistido por visitante; el precio real lo recalcula el API) ----------
  const cartKey = `crimson-shop:${slug}`;
  const [cart, setCart] = useState<Line[]>(() => { try { return JSON.parse(store.get(cartKey) || "[]") as Line[]; } catch { return []; } });
  useEffect(() => { store.set(cartKey, JSON.stringify(cart)); }, [cart, cartKey]);
  const [drawer, setDrawer] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [bump, setBump] = useState(0);
  const toastT = useRef<number | undefined>(undefined);
  const count = cart.reduce((a, l) => a + l.qty, 0);
  const estimate = cart.reduce((a, l) => a + l.qty * l.price, 0);
  const add = (p: Prod, variant?: Variant, qty = 1) => {
    const key = `${p.id}:${variant?.id ?? 0}`;
    const av = normAvail(p.availability);
    const max = av?.max_qty && av.max_qty > 0 ? av.max_qty : null;
    const cap = (n: number) => (max ? Math.min(max, n) : n);
    setCart((c) => {
      const found = c.find((l) => l.key === key);
      if (found) return c.map((l) => (l.key === key ? { ...l, qty: cap(l.qty + qty), max } : l));
      return [...c, { key, id: p.id, variant_id: variant?.id ?? null, name: variant ? `${p.name} · ${variant.name}` : p.name, image: p.image, price: Number(variant?.display_price ?? p.display_price), currency: p.display_currency, qty: cap(qty), max }];
    });
    const price = Number(variant?.display_price ?? p.display_price);
    track("add_to_cart", { currency: p.display_currency, value: price * qty, items: [{ item_id: String(p.id), item_name: variant ? `${p.name} · ${variant.name}` : p.name, price, quantity: qty }] });
    if (!calm() && navigator.userActivation?.isActive) navigator.vibrate?.(10);
    window.clearTimeout(toastT.current);
    setToast(p.name);
    setBump((b) => b + 1);
    toastT.current = window.setTimeout(() => setToast(null), 5000); // tiempo para tocar "Ir al carrito" (pedido 21/10)
  };
  const setQty = (key: string, qty: number) => setCart((c) => c.map((l) => (l.key === key ? { ...l, qty: Math.max(1, Math.min(l.max && l.max > 0 ? l.max : 999, qty || 1)) } : l)));
  const remove = (key: string) => setCart((c) => c.filter((l) => l.key !== key));
  useEffect(() => {
    if (!drawer) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawer(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [drawer]);

  // ---------- comparador (hasta 3, por visitante) ----------
  const cmpKey = `crimson-shop:cmp:${slug}`;
  const [compare, setCompare] = useState<number[]>(() => { try { return (JSON.parse(safeStore.get(cmpKey) || "[]") as number[]).slice(0, 3); } catch { return []; } });
  useEffect(() => { safeStore.set(cmpKey, JSON.stringify(compare)); }, [compare, cmpKey]);

  // ---------- buscador de la cabecera: en /buscar filtra mientras se escribe; en otra vista, al confirmar ----------
  const onSearch = rel === "/buscar";
  const [qInput, setQInput] = useState(onSearch ? sp.get("q") || "" : "");
  useEffect(() => { if (onSearch) setQInput(sp.get("q") || ""); }, [onSearch, sp]);
  useEffect(() => {
    if (!onSearch || qInput === (sp.get("q") || "")) return;
    const t = window.setTimeout(() => { const n = new URLSearchParams(sp); if (qInput.trim()) n.set("q", qInput.trim()); else n.delete("q"); setSp(n, { replace: true }); }, 300);
    return () => window.clearTimeout(t);
  }, [qInput]); // eslint-disable-line react-hooks/exhaustive-deps
  const submitSearch = (e: FormEvent) => {
    e.preventDefault();
    (e.currentTarget.querySelector("input") as HTMLInputElement | null)?.blur();
    if (qInput.trim()) navigate(`${base}/buscar?q=${encodeURIComponent(qInput.trim())}`);
  };

  const S = home?.store;
  const isShop = S?.kind === "tienda";
  const ctx = useMemo<StoreCtx | null>(() => (S ? {
    slug, base, hostMode, store: S, isShop: !!isShop,
    api: async <T,>(path: string, init?: RequestInit) => (await api<T>(path, init)).data,
    addToCart: (p: VProd, qty = 1, variant?: VVariant) => { if (!Number(variant?.display_price ?? p.display_price)) return; add(p as unknown as Prod, variant as Variant | undefined, qty); },
    openCart: () => setDrawer(true),
    compare,
    toggleCompare: (id: number) => setCompare((c) => (c.includes(id) ? c.filter((x) => x !== id) : c.length >= 3 ? c : [...c, id])),
  } : null), [S, slug, base, hostMode, isShop, api, compare]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!hostMode) {
      let m = document.querySelector<HTMLMetaElement>('meta[name="robots"]');
      if (!m) { m = document.createElement("meta"); m.name = "robots"; document.head.appendChild(m); }
      m.content = "noindex"; // dentro del portal no se indexa: la tienda indexable es la del dominio propio
    }
    if (v === "pagar" && S) document.title = `Finalizar pedido · ${S.name}`;
  }, [hostMode, v, S]);

  if (fatal && !home) return <div className="shop"><div className="sh-empty"><b>{fatal}</b><span>La tienda no está disponible en este momento.</span></div></div>;
  if (!home || !S || !ctx) return <div className="shop" style={{ display: "grid", placeItems: "center" }}><span className="spinner" /></div>;

  const hasWa = !!S.whatsapp;
  const quoteText = (lines: Line[]) => `Hola ${S.name}, quiero cotizar:\n${lines.map((l) => `• ${l.qty} × ${l.name}`).join("\n")}`;
  const slugs = catSlugs(home.categories);
  const L = (p: string) => `${base}${p}` || "/";
  const legalOn = (k: "terms" | "privacy") => !!S.legal?.[k];

  return (
    <StoreProvider value={ctx}>
    <div className="shop" style={{ fontFamily: undefined /* toda la tienda en Arial (var(--sans)) */ }}>
      <a className="sh-skip" href="#contenido">Saltar al contenido</a>
      <header className="sh-head vt-head">
        <div className="sh-wrap">
          <div className="sh-head__row">
            <Link className="sh-logo" to={L("/")} aria-label={`${S.name}, inicio de la tienda`}>
              {S.logo_url ? <img src={S.logo_url} alt="" height={30} /> : <b>{S.name}</b>}
              <span className="sh-meta">Tienda</span>
            </Link>
            <nav className="vt-mainnav" aria-label="Principal">
              <Link to={L("/catalogo")} aria-current={rel === "/catalogo" ? "page" : undefined}>Catálogo</Link>
              <Link to={L("/arma-tu-sistema")} aria-current={rel === "/arma-tu-sistema" ? "page" : undefined}>Arma tu sistema</Link>
              <Link to={L("/asesoria")} aria-current={rel === "/asesoria" ? "page" : undefined}>Asesoría</Link>
            </nav>
            <SearchBox value={qInput} onChange={setQInput} onSubmit={submitSearch} />
            <div className="sh-head__actions">
              {hasWa && <a className="sh-btn sh-btn--ghost sh-btn--sm sh-hide-m" href={wa(S.whatsapp, `Hola ${S.name}, tengo una consulta.`)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} /><span className="vt-hide-l">WhatsApp</span></a>}
              {compare.length > 0 && <Link className="sh-btn sh-btn--ghost sh-btn--sm vt-cmpbtn" to={L("/comparar")} aria-label={`Comparar ${compare.length} producto(s)`}><Ico d={IC.compare} /><span className="sh-badge sh-badge--ink">{compare.length}</span></Link>}
              <button className="sh-btn sh-btn--sm sh-cartbtn" data-bump={bump % 2} onClick={() => setDrawer(true)} aria-label={`${isShop ? "Carrito" : "Cotización"}: ${count} artículo(s)`}>
                <Ico d={IC.bag} /><span className="sh-hide-m">{isShop ? "Carrito" : "Cotización"}</span>
                {count > 0 && <span className="sh-badge" key={bump}>{count > 99 ? "99+" : count}</span>}
              </button>
            </div>
          </div>
          <div className="sh-head__search-m"><SearchBox value={qInput} onChange={setQInput} onSubmit={submitSearch} /></div>
          <nav className="vt-quick" aria-label="Accesos rápidos">
            <Link to={L("/arma-tu-sistema")} className="vt-quick__hot"><Ico d={IC.cam} size={16} />Arma tu sistema</Link>
            <Link to={L("/catalogo")}>Catálogo</Link>
            {home.categories.map((c) => <Link key={c.id} to={L(`/c/${slugs[c.id]}`)}>{c.name}</Link>)}
            <Link to={L("/asesoria")}>Asesoría</Link>
          </nav>
        </div>
      </header>

      <main id="contenido" tabIndex={-1} style={{ outline: "none" }}>
        {pedido ? (
          <OrderStatus key={pedido} api={api} token={pedido} store={S} onBack={() => go({})} />
        ) : v === "mi-pedido" ? (
          <OrderLookup api={api} onFound={(t) => go({ pedido: t })} onBack={() => go({})} />
        ) : v === "pagar" || v === "cotizar" ? (
          <Checkout api={api} slug={slug} store={S} cart={cart} mode={v === "pagar" && isShop ? "pedido" : "cotizacion"} onPlaced={(o) => { setCart([]); go({ pedido: o.track_token }); }} onBack={() => go({})} setQty={setQty} remove={remove} quoteText={quoteText} termsHref={L("/info/terminos")} />
        ) : (
          <Suspense fallback={<Spin />}>
            <Routes>
              <Route index element={<StoreHome />} />
              <Route path="catalogo" element={<CategoryPage key="all" mode="all" />} />
              <Route path="c/:cat" element={<CategoryPage key="cat" mode="category" />} />
              <Route path="buscar" element={<CategoryPage key="search" mode="search" />} />
              <Route path="p/:ref" element={<ProductPage />} />
              <Route path="arma-tu-sistema" element={<KitBuilder />} />
              <Route path="comparar" element={<ComparePage />} />
              <Route path="asesoria" element={<AdvisoryPage />} />
              <Route path="info/:kind" element={<InfoPage />} />
              <Route path="pg/:page" element={<BlockPage primary={S.primary} />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </Suspense>
        )}
      </main>

      <footer className="sh-foot">
        <div className="sh-wrap">
          <div className="sh-foot__grid">
            <div><b>{S.name}</b><span>{S.tagline || "Seguridad electrónica, redes y soporte técnico en Costa Rica."}</span>
              <span className="vt-foot__seal sh-meta">Factura electrónica · Soporte local · Garantía</span>
            </div>
            <div><span className="sh-meta">Comprar</span>
              <div className="vt-foot__col">
                <Link to={L("/catalogo")}>Catálogo completo</Link>
                {home.categories.slice(0, 5).map((c) => <Link key={c.id} to={L(`/c/${slugs[c.id]}`)}>{c.name}</Link>)}
                <Link to={L("/arma-tu-sistema")}>Arma tu sistema</Link>
              </div>
            </div>
            <div><span className="sh-meta">Ayuda</span>
              <div className="vt-foot__col">
                <Link to={L("/asesoria")}>Asesoría personalizada</Link>
                <Link to={L("/info/preguntas-frecuentes")}>Preguntas frecuentes</Link>
                <Link to={L("/info/envios")}>Envíos y devoluciones</Link>
                <Link to={L("/info/garantias")}>Garantías</Link>
                {hasWa && <a href={wa(S.whatsapp, `Hola ${S.name}, tengo una consulta.`)} target="_blank" rel="noopener noreferrer">WhatsApp {S.whatsapp}</a>}
              </div>
            </div>
            <div><span className="sh-meta">Crimson</span>
              <div className="vt-foot__col">
                <a href="https://crimsoncr.com" rel="noopener">crimsoncr.com</a>
                <a href="https://crimsoncr.com/#soporte" rel="noopener">Reportar una falla</a>
                {isShop && <button type="button" onClick={() => go({ v: "mi-pedido" })}>Mi pedido</button>}
                {legalOn("terms") && <Link to={L("/info/terminos")}>Términos y condiciones</Link>}
                {legalOn("privacy") && <Link to={L("/info/privacidad")}>Política de privacidad</Link>}
              </div>
            </div>
          </div>
          <div className="sh-foot__base"><span>© {new Date().getFullYear()} {S.name}</span><span>Precios en {S.currency === "USD" ? "dólares" : "colones"}, IVA incluido.</span></div>
        </div>
      </footer>

      <div className="sh-scrim" data-open={drawer} onClick={() => setDrawer(false)} />
      <aside className="sh-drawer" data-open={drawer} role="dialog" aria-modal="true" aria-label={isShop ? "Carrito" : "Cotización"} aria-hidden={!drawer}>
        <div className="sh-drawer__head"><h2>{isShop ? "Tu carrito" : "Tu cotización"}</h2><button className="sh-x" onClick={() => setDrawer(false)} aria-label="Cerrar"><Ico d={IC.x} /></button></div>
        <div className="sh-drawer__body">
          {cart.length === 0 ? <div className="sh-empty" style={{ padding: "40px 0" }}><b>Todavía no agregaste nada.</b><button className="sh-btn sh-btn--ghost" onClick={() => { setDrawer(false); navigate(L("/catalogo")); }}>Ver catálogo</button></div>
            : cart.map((l) => <CartLine key={l.key} l={l} setQty={setQty} remove={remove} />)}
        </div>
        {cart.length > 0 && (
          <div className="sh-drawer__foot">
            <div className="sh-totrow sh-totrow--big"><span>Total estimado</span><b>{money(estimate, cart[0].currency)}</b></div>
            <p className="sh-note" style={{ margin: 0 }}>IVA incluido. {isShop ? "El envío se calcula al finalizar." : "Te confirmamos precio final y disponibilidad."}</p>
            {isShop ? <button className="sh-btn sh-btn--crimson sh-btn--block" onClick={() => { setDrawer(false); go({ v: "pagar" }); }}>Finalizar pedido</button>
              : <button className="sh-btn sh-btn--crimson sh-btn--block" onClick={() => { setDrawer(false); go({ v: "cotizar" }); }}>Solicitar cotización</button>}
            {hasWa && <a className="sh-btn sh-btn--wa sh-btn--block" href={wa(S.whatsapp, quoteText(cart))} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />{isShop ? "Consultar por WhatsApp" : "Cotizar por WhatsApp"}</a>}
          </div>
        )}
      </aside>

      {toast && <div className="sh-toast" role="status"><Ico d={IC.check} /><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "38vw" }}>Agregado: {toast}</span><button onClick={() => { setToast(null); setDrawer(true); }}>Ir {isShop ? "al carrito" : "a la cotización"}</button></div>}

      <Consent />

      {compare.length > 0 && rel !== "/comparar" && !v && (rel.startsWith("/c/") || rel === "/catalogo" || rel === "/buscar") && (
        <div className="vt-cmptray" role="region" aria-label="Comparador">
          <span><Ico d={IC.compare} />{compare.length} de 3 para comparar</span>
          <button className="vt-link vt-link--light" onClick={() => setCompare([])}>Vaciar</button>
          <Link className="sh-btn sh-btn--sm sh-btn--crimson" to={L("/comparar")}>Comparar</Link>
        </div>
      )}
    </div>
    </StoreProvider>
  );
}

function SearchBox({ value, onChange, onSubmit }: { value: string; onChange: (v: string) => void; onSubmit: (e: FormEvent<HTMLFormElement>) => void }) {
  return (
    <form className="sh-search" role="search" onSubmit={onSubmit}>
      <Ico d={IC.search} />
      <input type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder="Buscar producto, marca o modelo" aria-label="Buscar productos" enterKeyHint="search" />
      {value && <button type="button" className="sh-search__clear" onClick={() => onChange("")} aria-label="Borrar búsqueda"><Ico d={IC.x} size={16} /></button>}
    </form>
  );
}

function CartLine({ l, setQty, remove }: { l: Line; setQty: (k: string, n: number) => void; remove: (k: string) => void }) {
  return (
    <div className="sh-line">
      <div className="sh-line__img">{l.image ? <img src={l.image} alt="" loading="lazy" decoding="async" /> : null}</div>
      <div style={{ minWidth: 0 }}>
        <div className="sh-line__name">{l.name}</div>
        <div className="sh-line__row">
          <Qty value={l.qty} onChange={(n) => setQty(l.key, n)} small />
          <button className="sh-rm" onClick={() => remove(l.key)}>Quitar</button>
        </div>
      </div>
      <div className="sh-line__total">{money(l.qty * l.price, l.currency)}</div>
    </div>
  );
}

function Qty({ value, onChange, small }: { value: number; onChange: (n: number) => void; small?: boolean }) {
  return (
    <span className={`sh-qty${small ? " sh-qty--sm" : ""}`}>
      <button type="button" onClick={() => onChange(Math.max(1, value - 1))} aria-label="Quitar uno">−</button>
      <input inputMode="numeric" value={value} onChange={(e) => onChange(Math.max(1, Math.min(999, Number(e.target.value.replace(/\D/g, "")) || 1)))} aria-label="Cantidad" />
      <button type="button" onClick={() => onChange(Math.min(999, value + 1))} aria-label="Agregar uno">+</button>
    </span>
  );
}


