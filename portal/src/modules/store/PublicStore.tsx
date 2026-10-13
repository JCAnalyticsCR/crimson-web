/* Vitrina publica: enrutador delgado. Vive en /tienda/:slug/* del portal y, con dominio propio
   (tienda.crimsoncr.com), en la raiz: ver StoreHost.tsx.

   Direcciones amigables: /  /catalogo  /c/<categoria>  /p/<nombre>-<id>  /buscar?q=  /arma-tu-sistema  /comparar
   /asesoria  /info/<terminos|privacidad|envios|garantias|preguntas-frecuentes>  /pg/<pagina de bloques>.
   Las de la version anterior (?p= ?c= ?q= ?legal= ?pg=) se redirigen a las nuevas.

   Esta pieza solo arma la cabecera, el pie y las rutas. La vitrina vive en StoreHome / CategoryPage / ProductPage /
   KitBuilder / StorePages; el carrito y el checkout (cajon, ?v=pagar|cotizar) siguen aqui abajo sin cambios y son
   del modulo de pedidos. Precios: los entrega el API ya en la moneda de la tienda y CON IVA (display_price). */
import { cloneElement, isValidElement, lazy, Suspense, useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent, type ReactElement, type ReactNode } from "react";
import { Link, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { type PubProduct } from "./Blocks";
import CategoryPage from "./CategoryPage";
import Consent from "./Consent";
import ProductPage from "./ProductPage";
import StoreHome from "./StoreHome";
import { catSlugs, safeStore, StoreProvider, type StoreCtx, type VProd, type Variant as VVariant } from "./storeKit";
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
type Line = { key: string; id: number; variant_id: number | null; name: string; image: string | null; price: number; currency: string; qty: number };
type Quote = { subtotal: number; discount_total: number; tax_total: number; shipping: number; total: number; currency: string };
type Done = { number: string; total: number; currency: string; instructions: string | null; whatsapp: string; lines?: { name: string; quantity: number; total: number }[] };

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
    setCart((c) => {
      const found = c.find((l) => l.key === key);
      if (found) return c.map((l) => (l.key === key ? { ...l, qty: l.qty + qty } : l));
      return [...c, { key, id: p.id, variant_id: variant?.id ?? null, name: variant ? `${p.name} · ${variant.name}` : p.name, image: p.image, price: Number(variant?.display_price ?? p.display_price), currency: p.display_currency, qty }];
    });
    if (!calm() && navigator.userActivation?.isActive) navigator.vibrate?.(10);
    window.clearTimeout(toastT.current);
    setToast(p.name);
    setBump((b) => b + 1);
    toastT.current = window.setTimeout(() => setToast(null), 2600);
  };
  const setQty = (key: string, qty: number) => setCart((c) => c.map((l) => (l.key === key ? { ...l, qty: Math.max(1, Math.min(999, qty || 1)) } : l)));
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
    <div className="shop" style={{ fontFamily: S.font && S.font !== "Manrope" ? `"${S.font}", var(--sans)` : undefined }}>
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
        {v === "pagar" || v === "cotizar" ? (
          <Checkout api={api} home={home} cart={cart} mode={v === "pagar" && isShop ? "pedido" : "cotizacion"} onDone={() => setCart([])} onBack={() => go({})} setQty={setQty} remove={remove} quoteText={quoteText} />
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

      {toast && <div className="sh-toast" role="status"><Ico d={IC.check} /><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "38vw" }}>Agregado: {toast}</span><button onClick={() => { setToast(null); setDrawer(true); }}>Ver {isShop ? "carrito" : "cotización"}</button></div>}

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

function Price({ value, cur, note = true }: { value: string | number; cur: string; note?: boolean }) {
  return <span className="sh-price">{money(value, cur)}{note && <small>IVA incluido</small>}</span>;
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

type ApiFn = <T>(path: string, init?: RequestInit) => Promise<{ data: T; total: number | null }>;

function Checkout({ api, home, cart, mode, onDone, onBack, setQty, remove, quoteText }: {
  api: ApiFn; home: Home; cart: Line[]; mode: "pedido" | "cotizacion"; onDone: () => void; onBack: () => void;
  setQty: (k: string, n: number) => void; remove: (k: string) => void; quoteText: (l: Line[]) => string;
}) {
  const S = home.store;
  const [f, setF] = useState({ name: "", email: "", phone: "", id_number: "", address: "", shipping_method: home.shipping_rates[0]?.name || "", payment_method: home.payment_methods[0]?.name || "", coupon_code: "", notes: "" });
  const [quote, setQuote] = useState<Quote | null>(null);
  const [qErr, setQErr] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Done | null>(null);
  const [sent, setSent] = useState(false);
  const lines = useMemo(() => cart.map((l) => ({ product_id: l.id, variant_id: l.variant_id, quantity: String(l.qty) })), [cart]);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  useEffect(() => {
    if (mode !== "pedido" || !lines.length) return;
    const t = window.setTimeout(() => {
      api<Quote>("/quote", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lines, contact: {}, shipping_method: f.shipping_method || null, coupon_code: f.coupon_code || null }) })
        .then(({ data }) => { setQuote(data); setQErr(null); }).catch((e) => { setQuote(null); setQErr(e.message); });
    }, 250);
    return () => window.clearTimeout(t);
  }, [api, lines, f.shipping_method, f.coupon_code, mode]);

  const okContact = f.name.trim().length > 1 && (/.+@.+\..+/.test(f.email) || f.phone.replace(/\D/g, "").length >= 8);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!okContact || busy) return;
    setBusy(true); setErr(null);
    try {
      if (mode === "pedido") {
        const { data } = await api<Done>("/checkout", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lines, contact: { name: f.name, email: f.email || null, phone: f.phone || null, id_number: f.id_number || null, address: f.address || null }, shipping_method: f.shipping_method || null, payment_method: f.payment_method || null, coupon_code: f.coupon_code || null, notes: f.notes || null }) });
        setDone(data); onDone();
      } else {
        if (!/.+@.+\..+/.test(f.email)) throw new Error("Indicá un correo para enviarte la cotización");
        const msg = `${quoteText(cart)}${f.notes ? `\n\nNotas: ${f.notes}` : ""}`;
        await api("/contact", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: f.name, email: f.email, phone: f.phone || null, message: msg.slice(0, 3900) }) });
        setSent(true); onDone();
      }
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };

  if (done) {
    const instr = done.instructions;
    return (
      <div className="sh-wrap"><div className="sh-done" role="status">
        <span className="sh-eyebrow">Pedido recibido</span>
        <h1>{done.number}</h1>
        <Price value={done.total} cur={done.currency} />
        {instr && <div className="sh-done__instr"><b style={{ display: "block", marginBottom: 6, color: "var(--text)" }}>Cómo pagar · {f.payment_method}</b>{instr}</div>}
        <p className="sh-note" style={{ margin: 0 }}>Te contactamos para coordinar la entrega. Con tu pago emitimos la factura electrónica.</p>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "center" }}>
          {done.whatsapp && <a className="sh-btn sh-btn--wa" href={wa(done.whatsapp, `Hola ${S.name}, hice el pedido ${done.number}. Adjunto el comprobante de pago.`)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />Enviar comprobante</a>}
          <button className="sh-btn sh-btn--ghost" onClick={onBack}>Seguir comprando</button>
        </div>
      </div></div>
    );
  }
  if (sent) {
    return <div className="sh-wrap"><div className="sh-done" role="status"><span className="sh-eyebrow">Solicitud enviada</span><h1>¡Gracias, {f.name.split(" ")[0]}!</h1><p className="sh-note" style={{ margin: 0, fontSize: 15 }}>Recibimos tu lista y te enviamos la cotización a {f.email}.</p><button className="sh-btn sh-btn--ghost" onClick={onBack}>Volver a la tienda</button></div></div>;
  }
  if (!cart.length) return <div className="sh-wrap sh-empty"><b>Tu {mode === "pedido" ? "carrito" : "cotización"} está vacío.</b><button className="sh-btn sh-btn--ghost" onClick={onBack}>Ver catálogo</button></div>;

  const cur = quote?.currency || cart[0].currency;
  return (
    <div className="sh-wrap">
      <nav className="sh-crumbs" aria-label="Ruta"><button onClick={onBack}><span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><Ico d={IC.back} size={14} />Seguir comprando</span></button></nav>
      <form className="sh-checkout" onSubmit={submit} noValidate>
        <div className="sh-form">
          <h1>{mode === "pedido" ? "Finalizar pedido" : "Solicitar cotización"}</h1>
          <fieldset className="sh-fieldset"><legend>Tus datos</legend>
            <div className="sh-2">
              <Field label="Nombre completo"><input autoComplete="name" value={f.name} onChange={set("name")} required /></Field>
              <Field label="Correo"><input type="email" autoComplete="email" value={f.email} onChange={set("email")} /></Field>
              <Field label="Teléfono / WhatsApp"><input type="tel" autoComplete="tel" value={f.phone} onChange={set("phone")} /></Field>
              {mode === "pedido" && <Field label="Cédula (para la factura)"><input value={f.id_number} onChange={set("id_number")} inputMode="numeric" /></Field>}
            </div>
            {mode === "pedido" && <Field label="Dirección de entrega"><input autoComplete="street-address" value={f.address} onChange={set("address")} /></Field>}
          </fieldset>
          {mode === "pedido" && home.shipping_rates.length > 0 && (
            <fieldset className="sh-fieldset"><legend>Entrega</legend>
              <div className="sh-choice">{home.shipping_rates.map((r) => (
                <label key={r.name} className="sh-opt"><input type="radio" name="ship" aria-label={r.name} checked={f.shipping_method === r.name} onChange={() => setF({ ...f, shipping_method: r.name })} /><span>{r.name}</span><span className="sh-meta">{r.per_kg ? "según peso" : Number(r.display_amount ?? r.amount) ? money(r.display_amount ?? r.amount, S.currency) : "gratis"}</span></label>
              ))}</div>
            </fieldset>
          )}
          {mode === "pedido" && home.payment_methods.length > 0 && (
            <fieldset className="sh-fieldset"><legend>Pago</legend>
              <div className="sh-choice">{home.payment_methods.map((m) => (
                <label key={m.name} className="sh-opt"><input type="radio" name="pay" aria-label={m.name} checked={f.payment_method === m.name} onChange={() => setF({ ...f, payment_method: m.name })} /><span>{m.name}<small>{m.instructions}</small></span><span /></label>
              ))}</div>
              <p className="sh-note" style={{ margin: 0 }}>Al confirmar te mostramos los datos para pagar. Despachamos al recibir el comprobante.</p>
            </fieldset>
          )}
          <fieldset className="sh-fieldset"><legend>Notas</legend>
            {mode === "pedido" && <Field label="Cupón"><input value={f.coupon_code} onChange={(e) => setF({ ...f, coupon_code: e.target.value.toUpperCase() })} autoCapitalize="characters" /></Field>}
            <Field label={mode === "pedido" ? "Notas del pedido" : "¿Algo más que debamos saber? (instalación, plazos…)"}><textarea value={f.notes} onChange={set("notes")} /></Field>
          </fieldset>
        </div>
        <aside className="sh-summary sh-panel" aria-label="Resumen">
          <span className="sh-meta">Resumen · {cart.reduce((a, l) => a + l.qty, 0)} artículo(s)</span>
          <div>{cart.map((l) => <CartLine key={l.key} l={l} setQty={setQty} remove={remove} />)}</div>
          {mode === "pedido" ? (
            quote ? (
              <div style={{ display: "grid", gap: 6 }}>
                <div className="sh-totrow"><span>Subtotal (sin IVA)</span><b>{money(quote.subtotal, cur)}</b></div>
                {Number(quote.discount_total) > 0 && <div className="sh-totrow"><span>Descuento</span><b>−{money(quote.discount_total, cur)}</b></div>}
                <div className="sh-totrow"><span>Envío</span><b>{money(quote.shipping, cur)}</b></div>
                <div className="sh-totrow"><span>IVA</span><b>{money(quote.tax_total, cur)}</b></div>
                <div className="sh-totrow sh-totrow--big"><span>Total</span><b>{money(quote.total, cur)}</b></div>
              </div>
            ) : <p className="sh-note">{qErr || "Calculando total…"}</p>
          ) : <p className="sh-note" style={{ margin: 0 }}>Te respondemos con precio final, disponibilidad y tiempos de entrega.</p>}
          {err && <p className="sh-err" role="alert" style={{ margin: 0 }}>{err}</p>}
          {!okContact && <p className="sh-note" style={{ margin: 0 }}>Completá tu nombre y un correo o teléfono para continuar.</p>}
          <button className="sh-btn sh-btn--crimson sh-btn--block" type="submit" disabled={!okContact || busy || (mode === "pedido" && !quote)}>{busy ? "Enviando…" : mode === "pedido" ? "Confirmar pedido" : "Enviar solicitud"}</button>
          {S.whatsapp && mode === "cotizacion" && <a className="sh-btn sh-btn--wa sh-btn--block" href={wa(S.whatsapp, quoteText(cart))} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />Mejor por WhatsApp</a>}
        </aside>
      </form>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  // etiqueta asociada por id (lectores de pantalla y autocompletar del navegador)
  const id = useId();
  const el = isValidElement(children) ? cloneElement(children as ReactElement<{ id?: string }>, { id }) : children;
  return <div className="sh-field"><label htmlFor={id}>{label}</label>{el}</div>;
}
