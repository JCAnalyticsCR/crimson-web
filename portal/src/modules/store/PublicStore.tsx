/* Vitrina publica: catalogo con categorias y buscador, ficha con fotos, carrito y pedido (o cotizacion por WhatsApp
   cuando la tienda es solo catalogo). Sin sesion. Vive en /tienda/:slug del portal y, con dominio propio
   (tienda.crimsoncr.com), en la raiz: ver StoreHost.tsx.

   Estado en la URL (?p= producto, ?c= categoria, ?q= busqueda, ?orden=, ?v=pagar|cotizar, ?pg= pagina, ?legal=):
   el boton atras funciona, los enlaces se comparten y Google ve cada ficha. Precios: los entrega el API ya en la
   moneda de la tienda y CON IVA (display_price); el pedido lo cotiza el API con el mismo motor que la factura. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Blocks, type Block, type PubProduct } from "./Blocks";
import Checkout from "./Checkout";
import OrderStatus, { OrderLookup } from "./OrderStatus";
import "./shop.css";

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
type Detail = Prod & { related: Prod[] };
type Line = { key: string; id: number; variant_id: number | null; name: string; image: string | null; price: number; currency: string; qty: number };

const PAGE = 24;
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
};

export default function PublicStore({ slug: slugProp, hostMode = false }: { slug?: string; hostMode?: boolean }) {
  const params = useParams();
  const slug = slugProp || params.slug || "";
  const [sp, setSp] = useSearchParams();
  const pid = Number(sp.get("p")) || null;
  const cat = Number(sp.get("c")) || null;
  const q = sp.get("q") || "";
  const sort = sp.get("orden") || "name";
  const v = sp.get("v");
  const pg = sp.get("pg");
  const legal = sp.get("legal") as "terms" | "privacy" | null;
  const pedido = sp.get("pedido"); // seguimiento del pedido (enlace firmado del correo)

  const api = useCallback(async <T,>(path: string, init?: RequestInit): Promise<{ data: T; total: number | null }> => {
    const r = await fetch(`/api/public/store/${slug}${path}`, init);
    if (!r.ok) { let m = "No se pudo cargar"; try { m = (await r.json()).detail || m; } catch { /* sin cuerpo */ } throw new Error(m); }
    const t = r.headers.get("X-Total-Count");
    return { data: (await r.json()) as T, total: t ? Number(t) : null };
  }, [slug]);

  const go = useCallback((next: Record<string, string | number | null | undefined>, opts?: { keep?: boolean; replace?: boolean }) => {
    const n = new URLSearchParams(opts?.keep ? sp : undefined);
    for (const [k, val] of Object.entries(next)) { if (val === null || val === undefined || val === "") n.delete(k); else n.set(k, String(val)); }
    setSp(n, { replace: opts?.replace });
    if (!opts?.replace) window.scrollTo({ top: 0, behavior: calm() ? "auto" : "smooth" });
  }, [sp, setSp]);

  // ---------- portada ----------
  const [home, setHome] = useState<Home | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [page, setPage] = useState<{ title: string; blocks: Block[] } | null>(null);
  useEffect(() => { api<Home>("").then(({ data }) => setHome(data)).catch((e) => setFatal(e.message)); }, [api]);
  const homeSlug = home?.nav.find((n) => n.slug === "inicio")?.slug;
  useEffect(() => {
    const want = pg || homeSlug;
    if (!want) { setPage(null); return; }
    api<{ title: string; blocks: Block[] }>(`/pages/${want}`).then(({ data }) => setPage(data)).catch(() => setPage(null));
  }, [api, pg, homeSlug]);

  // ---------- carrito (persistido por visitante; el precio real lo recalcula el API) ----------
  const cartKey = `crimson-shop:${slug}`;
  const [cart, setCart] = useState<Line[]>(() => { try { return JSON.parse(store.get(cartKey) || "[]") as Line[]; } catch { return []; } });
  useEffect(() => { store.set(cartKey, JSON.stringify(cart)); }, [cart, cartKey]);
  const [drawer, setDrawer] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
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

  // ---------- catalogo ----------
  const listing = !pid && !v && !pedido;
  const [qInput, setQInput] = useState(q);
  useEffect(() => setQInput(q), [q]);
  useEffect(() => {
    if (qInput === q) return;
    const t = window.setTimeout(() => go({ q: qInput.trim() || null, p: null, v: null, pg: null }, { keep: true, replace: true }), 300);
    return () => window.clearTimeout(t);
  }, [qInput]); // eslint-disable-line react-hooks/exhaustive-deps
  const [items, setItems] = useState<Prod[] | null>(null);
  const [total, setTotal] = useState(0);
  const [more, setMore] = useState(false);
  const listQs = useMemo(() => { const s = new URLSearchParams({ limit: String(PAGE), compact: "true", sort }); if (q) s.set("q", q); if (cat) s.set("category_id", String(cat)); return s; }, [q, cat, sort]);
  useEffect(() => {
    if (!listing || !home) return;
    let alive = true;
    setItems(null);
    api<Prod[]>(`/products?${listQs}&offset=0`).then(({ data, total }) => { if (alive) { setItems(data); setTotal(total ?? data.length); } }).catch(() => alive && setItems([]));
    return () => { alive = false; };
  }, [listing, home, listQs, api]);
  const loadMore = () => {
    if (!items) return;
    setMore(true);
    api<Prod[]>(`/products?${listQs}&offset=${items.length}`).then(({ data }) => setItems([...items, ...data])).finally(() => setMore(false));
  };

  // ---------- ficha ----------
  const [detail, setDetail] = useState<Detail | null>(null);
  const [photo, setPhoto] = useState(0);
  const [variant, setVariant] = useState<Variant | undefined>();
  const [qty, setQtyD] = useState(1);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    if (!pid) { setDetail(null); return; }
    setDetail(null); setMissing(false); setPhoto(0); setQtyD(1);
    api<Detail>(`/products/${pid}`).then(({ data }) => { setDetail(data); setVariant(data.variants?.[0]); }).catch(() => setMissing(true));
  }, [pid, api]);

  // ---------- SEO (titulo y descripcion por vista; indexable solo en el dominio de la tienda) ----------
  useEffect(() => {
    if (!home) return;
    const name = home.store.name;
    const catName = home.categories.find((c) => c.id === cat)?.name;
    document.title = detail ? `${detail.name} · ${name}` : pedido || v === "mi-pedido" ? `Mi pedido · ${name}` : v === "pagar" ? `Finalizar pedido · ${name}` : catName ? `${catName} · ${name}` : `Tienda · ${name}`;
    const desc = detail?.description?.slice(0, 160) || home.store.tagline || `Catálogo en línea de ${name}`;
    const meta = (n: string, val: string) => { let m = document.querySelector<HTMLMetaElement>(`meta[name="${n}"]`); if (!m) { m = document.createElement("meta"); m.name = n; document.head.appendChild(m); } m.content = val; };
    meta("description", desc);
    meta("robots", hostMode && !pedido && v !== "pagar" && v !== "mi-pedido" ? "index,follow" : "noindex");
    document.documentElement.lang = "es-CR";
  }, [home, detail, cat, v, hostMode, pedido]);

  if (fatal && !home) return <div className="shop"><div className="sh-empty"><b>{fatal}</b><span>La tienda no está disponible en este momento.</span></div></div>;
  if (!home) return <div className="shop" style={{ display: "grid", placeItems: "center" }}><span className="spinner" /></div>;

  const S = home.store;
  const isShop = S.kind === "tienda";
  const cur = items?.[0]?.display_currency || S.currency || "CRC";
  const catName = (id: number | null) => home.categories.find((c) => c.id === id)?.name;
  const toCatalog = (c?: number | null) => go({ c: c || null });
  const hasWa = !!S.whatsapp;
  const quoteText = (lines: Line[]) => `Hola ${S.name}, quiero cotizar:\n${lines.map((l) => `• ${l.qty} × ${l.name}`).join("\n")}`;

  return (
    <div className="shop" style={{ fontFamily: S.font && S.font !== "Manrope" ? `"${S.font}", var(--sans)` : undefined }}>
      <a className="sh-skip" href="#contenido">Saltar al contenido</a>
      <header className="sh-head">
        <div className="sh-wrap">
          <div className="sh-head__row">
            <button className="sh-logo" onClick={() => go({})} aria-label={`${S.name}, inicio de la tienda`}>
              {S.logo_url ? <img src={S.logo_url} alt="" height={30} /> : <b>{S.name}</b>}
              <span className="sh-meta">Tienda</span>
            </button>
            <SearchBox value={qInput} onChange={setQInput} />
            <div className="sh-head__actions">
              {home.nav.filter((n) => n.slug !== "inicio").slice(0, 3).map((n) => <button key={n.slug} className="sh-btn sh-btn--ghost sh-btn--sm sh-hide-m" onClick={() => go({ pg: n.slug })}>{n.title}</button>)}
              {hasWa && <a className="sh-btn sh-btn--ghost sh-btn--sm sh-hide-m" href={wa(S.whatsapp, `Hola ${S.name}, tengo una consulta.`)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />WhatsApp</a>}
              <button className="sh-btn sh-btn--sm sh-cartbtn" onClick={() => setDrawer(true)} aria-label={`${isShop ? "Carrito" : "Cotización"}: ${count} artículo(s)`}>
                <Ico d={IC.bag} /><span className="sh-hide-m">{isShop ? "Carrito" : "Cotización"}</span>
                {count > 0 && <span className="sh-badge">{count > 99 ? "99+" : count}</span>}
              </button>
            </div>
          </div>
          <div className="sh-head__search-m"><SearchBox value={qInput} onChange={setQInput} /></div>
        </div>
      </header>

      <main id="contenido" tabIndex={-1} style={{ outline: "none" }}>
        {pg && page ? (
          <Blocks slug={slug} blocks={page.blocks} primary={S.primary} products={(items || []) as unknown as PubProduct[]} onCta={(to) => go(to === "productos" ? {} : { pg: to })} onProduct={(id) => go({ p: id })} />
        ) : pedido ? (
          <OrderStatus key={pedido} api={api} token={pedido} store={S} onBack={() => go({})} />
        ) : v === "mi-pedido" ? (
          <OrderLookup api={api} onFound={(t) => go({ pedido: t })} onBack={() => go({})} />
        ) : pid ? (
          <ProductView d={detail} missing={missing} cur={cur} isShop={isShop} store={S} photo={photo} setPhoto={setPhoto} variant={variant} setVariant={setVariant} qty={qty} setQty={setQtyD}
            onAdd={(p, vv, n) => add(p, vv, n)} onBuy={(p, vv, n) => { add(p, vv, n); go({ v: "pagar" }); }} onCat={toCatalog} onOpen={(id) => go({ p: id })} catName={catName} />
        ) : v === "pagar" || v === "cotizar" ? (
          <Checkout api={api} store={S} cart={cart} mode={v === "pagar" && isShop ? "pedido" : "cotizacion"} onPlaced={(o) => { setCart([]); go({ pedido: o.track_token }); }} onBack={() => go({})} setQty={setQty} remove={remove} quoteText={quoteText} onTerms={() => go({ legal: "terms" }, { keep: true })} />
        ) : (
          <>
            {page && homeSlug ? (
              <Blocks slug={slug} blocks={page.blocks} primary={S.primary} products={(items || []) as unknown as PubProduct[]} onCta={(to) => (to === "productos" ? document.getElementById("catalogo")?.scrollIntoView() : go({ pg: to }))} onProduct={(id) => go({ p: id })} />
            ) : (
              <section className="sh-hero" aria-labelledby="sh-title">
                <div className="sh-wrap">
                  <span className="sh-eyebrow">Tienda en línea · {S.name}</span>
                  <h1 id="sh-title">{S.tagline || <>Equipo profesional, <em>listo para instalar</em>.</>}</h1>
                  <div className="sh-hero__facts sh-meta">
                    <span><i className="sh-live" />{home.product_count ?? "—"} productos</span>
                    <span>Precios con IVA incluido</span>
                    <span>{isShop ? "Compra directa · factura electrónica" : "Cotización sin compromiso"}</span>
                  </div>
                </div>
              </section>
            )}
            <section id="catalogo" className="sh-wrap" aria-label="Catálogo">
              <div className="sh-cats" role="toolbar" aria-label="Categorías">
                <button className="sh-chip" aria-pressed={!cat} onClick={() => toCatalog(null)}>Todo <i>{home.product_count ?? ""}</i></button>
                {home.categories.map((c) => <button key={c.id} className="sh-chip" aria-pressed={cat === c.id} onClick={() => toCatalog(c.id)}>{c.name} <i>{c.count}</i></button>)}
              </div>
              <div className="sh-bar">
                <span className="sh-meta" aria-live="polite">{items ? `${total} resultado${total === 1 ? "" : "s"}${q ? ` para “${q}”` : ""}${cat ? ` · ${catName(cat) || ""}` : ""}` : "Cargando…"}</span>
                <label className="sh-meta" style={{ display: "flex", alignItems: "center", gap: 8 }}>Ordenar
                  <select className="sh-select" value={sort} onChange={(e) => go({ orden: e.target.value === "name" ? null : e.target.value }, { keep: true, replace: true })}>
                    <option value="name">Nombre</option><option value="price_asc">Precio: menor a mayor</option><option value="price_desc">Precio: mayor a menor</option><option value="recent">Recientes</option>
                  </select>
                </label>
              </div>
              {items === null ? (
                <div className="sh-grid" aria-hidden="true">{Array.from({ length: 8 }, (_, i) => <div key={i} className="sh-skel" style={{ aspectRatio: "3/4" }} />)}</div>
              ) : items.length === 0 ? (
                <div className="sh-empty"><b>No encontramos productos{q ? ` para “${q}”` : ""}.</b><span>Probá con otra palabra o escribinos y lo conseguimos.</span>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "center" }}>
                    {(q || cat) && <button className="sh-btn sh-btn--ghost" onClick={() => { setQInput(""); go({}); }}>Ver todo el catálogo</button>}
                    {hasWa && <a className="sh-btn sh-btn--wa" href={wa(S.whatsapp, `Hola ${S.name}, busco: ${q || "un producto"}`)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />Preguntar por WhatsApp</a>}
                  </div>
                </div>
              ) : (
                <>
                  <ul className="sh-grid" style={{ listStyle: "none", margin: 0, padding: 0 }}>
                    {items.map((p) => <li key={p.id} style={{ display: "flex" }}><Card p={p} isShop={isShop} onOpen={() => go({ p: p.id })} onAdd={() => (p.variants?.length ? go({ p: p.id }) : add(p))} /></li>)}
                  </ul>
                  <div className="sh-more">
                    <span className="sh-meta">{items.length} de {total}</span>
                    {items.length < total && <button className="sh-btn sh-btn--ghost" onClick={loadMore} disabled={more}>{more ? "Cargando…" : "Ver más productos"}</button>}
                  </div>
                </>
              )}
            </section>
          </>
        )}
      </main>

      <footer className="sh-foot">
        <div className="sh-wrap">
          <div className="sh-foot__grid">
            <div><b>{S.name}</b><span>{S.tagline || "Tienda en línea"}</span></div>
            <div><span className="sh-meta">Comprar</span>
              <div style={{ display: "grid" }}>
                <button onClick={() => go({})}>Catálogo completo</button>
                {home.categories.slice(0, 4).map((c) => <button key={c.id} onClick={() => toCatalog(c.id)}>{c.name}</button>)}
              </div>
            </div>
            <div><span className="sh-meta">Ayuda</span>
              <div style={{ display: "grid" }}>
                {hasWa && <a href={wa(S.whatsapp, `Hola ${S.name}, tengo una consulta.`)} target="_blank" rel="noopener noreferrer">WhatsApp {S.whatsapp}</a>}
                {isShop && <button onClick={() => go({ v: "mi-pedido" })}>Mi pedido</button>}
                {S.legal?.terms && <button onClick={() => go({ legal: "terms" }, { keep: true })}>Términos y condiciones</button>}
                {S.legal?.privacy && <button onClick={() => go({ legal: "privacy" }, { keep: true })}>Política de privacidad</button>}
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
          {cart.length === 0 ? <div className="sh-empty" style={{ padding: "40px 0" }}><b>Todavía no agregaste nada.</b><button className="sh-btn sh-btn--ghost" onClick={() => { setDrawer(false); go({}); }}>Ver catálogo</button></div>
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

      {legal && S.legal?.[legal] && (
        <div className="sh-modal" role="dialog" aria-modal="true" aria-label={legal === "terms" ? "Términos y condiciones" : "Política de privacidad"} onClick={() => go({ legal: null }, { keep: true, replace: true })}>
          <div className="sh-modal__box" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 12 }}>
              <h2>{legal === "terms" ? "Términos y condiciones" : "Política de privacidad"}</h2>
              <button className="sh-x" onClick={() => go({ legal: null }, { keep: true, replace: true })} aria-label="Cerrar"><Ico d={IC.x} /></button>
            </div>
            <div className="sh-desc">{S.legal[legal]}</div>
          </div>
        </div>
      )}
    </div>
  );
}

function SearchBox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <form className="sh-search" role="search" onSubmit={(e) => { e.preventDefault(); (e.currentTarget.querySelector("input") as HTMLInputElement)?.blur(); }}>
      <Ico d={IC.search} />
      <input type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder="Buscar cámaras, switches, UPS…" aria-label="Buscar productos" enterKeyHint="search" />
      {value && <button type="button" className="sh-search__clear" onClick={() => onChange("")} aria-label="Borrar búsqueda"><Ico d={IC.x} size={16} /></button>}
    </form>
  );
}

function Price({ value, cur, note = true }: { value: string | number; cur: string; note?: boolean }) {
  return <span className="sh-price">{money(value, cur)}{note && <small>IVA incluido</small>}</span>;
}

function Card({ p, isShop, onOpen, onAdd }: { p: Prod; isShop: boolean; onOpen: () => void; onAdd: () => void }) {
  const [done, setDone] = useState(false);
  const href = `?p=${p.id}`;
  return (
    <article className="sh-card" style={{ width: "100%" }}>
      <a className="sh-card__link" href={href} onClick={(e) => { if (e.metaKey || e.ctrlKey || e.shiftKey) return; e.preventDefault(); onOpen(); }}>
        <div className="sh-card__img">{p.image ? <img src={p.image} alt="" loading="lazy" decoding="async" width={400} height={400} /> : <span className="sh-noimg">Sin foto</span>}</div>
      </a>
      <div className="sh-card__body">
        {p.category && <span className="sh-meta" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.category}</span>}
        <a className="sh-card__name" href={href} onClick={(e) => { if (e.metaKey || e.ctrlKey || e.shiftKey) return; e.preventDefault(); onOpen(); }}>{p.name}</a>
        <div className="sh-card__foot">
          <Price value={p.display_price} cur={p.display_currency} />
          <button className="sh-add" data-done={done} onClick={() => { onAdd(); if (!p.variants?.length) { setDone(true); window.setTimeout(() => setDone(false), 1200); } }}
            aria-label={p.variants?.length ? `Ver opciones de ${p.name}` : `${isShop ? "Agregar al carrito" : "Agregar a la cotización"}: ${p.name}`}>
            <Ico d={done ? IC.check : IC.plus} />
          </button>
        </div>
      </div>
    </article>
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

function ProductView(props: {
  d: Detail | null; missing: boolean; cur: string; isShop: boolean; store: Home["store"]; photo: number; setPhoto: (n: number) => void;
  variant?: Variant; setVariant: (v?: Variant) => void; qty: number; setQty: (n: number) => void;
  onAdd: (p: Prod, v: Variant | undefined, n: number) => void; onBuy: (p: Prod, v: Variant | undefined, n: number) => void;
  onCat: (c: number | null) => void; onOpen: (id: number) => void; catName: (id: number | null) => string | undefined;
}) {
  const { d, store: S } = props;
  if (props.missing) return <div className="sh-wrap sh-empty"><b>Este producto ya no está disponible.</b><button className="sh-btn sh-btn--ghost" onClick={() => props.onCat(null)}>Ver catálogo</button></div>;
  if (!d) return <div className="sh-wrap" style={{ paddingTop: 24 }}><div className="sh-pdp"><div className="sh-skel" style={{ aspectRatio: "1" }} /><div style={{ display: "grid", gap: 12 }}><div className="sh-skel" style={{ height: 40 }} /><div className="sh-skel" style={{ height: 120 }} /></div></div></div>;
  const imgs = d.images?.length ? d.images : d.image ? [d.image] : [];
  const price = props.variant?.display_price ?? d.display_price;
  const avail = d.availability;
  const waText = `Hola ${S.name}, me interesa: ${d.name}${d.code ? ` (${d.code})` : ""}`;
  return (
    <div className="sh-wrap">
      <nav className="sh-crumbs" aria-label="Ruta">
        <button onClick={() => props.onCat(null)}><span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><Ico d={IC.back} size={14} />Catálogo</span></button>
        {d.category_id && <><span aria-hidden="true">/</span><button onClick={() => props.onCat(d.category_id)}>{d.category || props.catName(d.category_id)}</button></>}
      </nav>
      <div className="sh-pdp">
        <div className="sh-gallery">
          <div className="sh-gallery__main">{imgs.length ? <img src={imgs[Math.min(props.photo, imgs.length - 1)]} alt={d.name} fetchPriority="high" decoding="async" width={800} height={800} /> : <span className="sh-noimg">Sin foto</span>}</div>
          {imgs.length > 1 && (
            <div className="sh-thumbs" role="list">
              {imgs.map((u, i) => <button key={u + i} className="sh-thumb" role="listitem" aria-current={i === props.photo} aria-label={`Foto ${i + 1} de ${imgs.length}`} onClick={() => props.setPhoto(i)}><img src={u} alt="" loading="lazy" decoding="async" /></button>)}
            </div>
          )}
        </div>
        <div className="sh-info">
          {d.category && <span className="sh-eyebrow">{d.category}</span>}
          <h1>{d.name}</h1>
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
            <Price value={price} cur={d.display_currency} />
            {avail && avail.state !== "servicio" && <span className="sh-avail" data-s={avail.state}>{avail.label}</span>}
          </div>
          {(d.variants?.length ?? 0) > 0 && (
            <div><div className="sh-meta" style={{ marginBottom: 8 }}>Opciones</div>
              <div className="sh-variants">{d.variants.map((vv) => <button key={vv.id} className="sh-chip" aria-pressed={props.variant?.id === vv.id} onClick={() => props.setVariant(vv)}>{vv.name}</button>)}</div>
            </div>
          )}
          <div className="sh-panel">
            <div className="sh-buy">
              <Qty value={props.qty} onChange={props.setQty} />
              {props.isShop
                ? <button className="sh-btn sh-btn--crimson" onClick={() => props.onAdd(d, props.variant, props.qty)}><Ico d={IC.bag} />Agregar al carrito</button>
                : <button className="sh-btn sh-btn--crimson" onClick={() => props.onAdd(d, props.variant, props.qty)}><Ico d={IC.plus} />Agregar a cotización</button>}
            </div>
            {props.isShop && <button className="sh-btn sh-btn--block" onClick={() => props.onBuy(d, props.variant, props.qty)}>Comprar ahora</button>}
            {S.whatsapp && <a className="sh-btn sh-btn--ghost sh-btn--block" href={wa(S.whatsapp, waText)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />Consultar por WhatsApp</a>}
          </div>
          {d.description && <div><h2 className="sh-meta" style={{ margin: "0 0 8px" }}>Descripción</h2><p className="sh-desc" style={{ margin: 0 }}>{d.description}</p></div>}
          <dl className="sh-specs">
            {d.code && <><dt>Código</dt><dd>{d.code}</dd></>}
            {d.brand && <><dt>Marca</dt><dd>{d.brand}</dd></>}
            <dt>Impuesto</dt><dd>IVA {d.tax_rate} % incluido en el precio</dd>
          </dl>
        </div>
      </div>
      {d.related?.length > 0 && (
        <section aria-label="Relacionados" style={{ paddingBottom: 20 }}>
          <h2 className="sh-section-title">También te puede servir</h2>
          <ul className="sh-grid" style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {d.related.map((r) => <li key={r.id} style={{ display: "flex" }}><Card p={r} isShop={props.isShop} onOpen={() => props.onOpen(r.id)} onAdd={() => (r.variants?.length ? props.onOpen(r.id) : props.onAdd(r, undefined, 1))} /></li>)}
          </ul>
        </section>
      )}
    </div>
  );
}
