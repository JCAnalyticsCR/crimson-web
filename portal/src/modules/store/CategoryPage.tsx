/* Catalogo: /catalogo, /c/<categoria> y /buscar?q=. Filtros (marca, tipo, precio, disponibilidad), orden y "ver mas".
   Todo el estado vive en la URL: atras funciona, se comparte, y Google ve cada categoria en su propia direccion.
   En movil los filtros van en una hoja inferior (solo se mueve lo que el usuario abre).
   Cuando el snapshot está disponible, el filtrado y las facetas son instantáneos (sin llamada al API).
   Si no hay snapshot (primera visita o falla), cae al endpoint /catalog como antes. */
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Crumbs, EmptyHelp, Grid, IC, Ico, Skeleton, money, useAvailability, useHead, usePath, useStore, type VProd } from "./storeKit";
import { filterCatalog, snapToVProd, type CatalogResult } from "./catalogFilter";
import { useSnapshot } from "./useSnapshot";

type Facets = { brands: { name: string; count: number }[]; types: { name: string; count: number }[]; availability: { disponible: number; bajo_pedido: number }; price: { min: number; max: number } };
type CatInfo = { id: number; name: string; slug: string; description: string | null; parent: { name: string; path: string } | null; children: { name: string; path: string }[] };
type NavCat = { name: string; path: string; count: number };
type Res = { items: VProd[]; total: number; category: CatInfo | null; facets: Facets; currency: string; nav: NavCat[] };

const PAGE = 24;
const SORTS: [string, string][] = [["relevance", "Relevancia"], ["price_asc", "Precio: menor a mayor"], ["price_desc", "Precio: mayor a menor"], ["name", "Nombre"], ["recent", "Novedades"]];

export default function CategoryPage({ mode }: { mode: "all" | "category" | "search" }) {
  const ctx = useStore();
  const to = usePath();
  const nav = useNavigate();
  const loc = useLocation();
  const { cat } = useParams();
  const [sp, setSp] = useSearchParams();
  const q = mode === "search" ? sp.get("q") || "" : "";
  const brands = sp.getAll("marca");
  const tipo = sp.get("tipo") || "";
  const min = sp.get("min") || "";
  const max = sp.get("max") || "";
  const disp = sp.get("disp") || "";
  const sort = sp.get("orden") || "relevance";

  const qs = useMemo(() => {
    const s = new URLSearchParams({ limit: String(PAGE), sort });
    if (q) s.set("q", q);
    if (mode === "category" && cat) s.set("category", cat);
    brands.forEach((b) => s.append("brand", b));
    if (tipo) s.set("type", tipo);
    if (min) s.set("min_price", min);
    if (max) s.set("max_price", max);
    if (disp) s.set("avail", disp);
    return s.toString();
  }, [q, mode, cat, brands.join("|"), tipo, min, max, disp, sort]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Snapshot: filtrado instantáneo en el navegador ─────────────────────────
  const { snap } = useSnapshot(ctx.slug);

  /** Resultado completo del filtrado desde el snapshot (sin límite de página). */
  const snapResult = useMemo((): CatalogResult | null => {
    if (!snap) return null;
    return filterCatalog(snap, {
      q: mode === "search" ? q : undefined,
      category: mode === "category" ? cat : undefined,
      brands: brands.length ? brands : undefined,
      tipo: tipo || undefined,
      min: min ? Number(min.replace(/\D/g, "")) : undefined,
      max: max ? Number(max.replace(/\D/g, "")) : undefined,
      avail: disp || undefined,
      sort,
      limit: 99999, // todos los resultados; paginamos localmente con displayLimit
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snap, q, mode, cat, brands.join("|"), tipo, min, max, disp, sort]);

  /** Cuántos productos mostrar en el modo snapshot (crece con "Cargar más"). */
  const [displayLimit, setDisplayLimit] = useState(PAGE);
  useEffect(() => { setDisplayLimit(PAGE); }, [qs]);

  // ── Estado de la respuesta (API o snapshot) ────────────────────────────────
  const [res, setRes] = useState<Res | null>(null);
  const [items, setItems] = useState<VProd[]>([]);
  const [missing, setMissing] = useState(false);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);

  // Cuando hay snapshot: derivar res e items sincrónicamente
  useEffect(() => {
    if (!snapResult) return;
    const sliced = snapResult.items.slice(0, displayLimit);
    const vprods = sliced.map((p) => snapToVProd(p, snapResult.currency, snap!.categories));
    setItems(vprods);
    setRes({
      items: vprods,
      total: snapResult.total,
      category: snapResult.category,
      facets: snapResult.facets,
      currency: snapResult.currency,
      nav: snapResult.nav,
    } as Res);
    setLoading(false);
    // Redirigir /c/5 → /c/<slug> con datos del snapshot
    if (mode === "category" && snapResult.category && cat !== snapResult.category.slug) {
      nav({ pathname: to(`/c/${snapResult.category.slug}`), search: loc.search }, { replace: true });
    }
  }, [snapResult, displayLimit]); // eslint-disable-line react-hooks/exhaustive-deps

  // Sin snapshot: llamar al API (ruta de respaldo)
  // al cambiar un filtro se mantiene lo anterior a la vista (atenuado) hasta que llega lo nuevo: nada salta
  useEffect(() => {
    if (snapResult) return; // snapshot disponible: sin llamada al API
    let alive = true;
    setLoading(true); setMissing(false);
    ctx.api<Res>(`/catalog?${qs}&offset=0`).then((r) => { if (!alive) return; setRes(r); setItems(r.items); }).catch(() => alive && setMissing(true)).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [qs, ctx, snapResult]); // eslint-disable-line react-hooks/exhaustive-deps

  // otra categoria u otra busqueda: eso si es otra pagina (en modo API; con snapshot el efecto de arriba ya pinta la
  // categoria nueva en este mismo render y limpiarla aqui la dejaba en "Cargando…" para siempre)
  useEffect(() => { if (!snapResult) { setRes(null); setItems([]); } }, [mode, cat]); // eslint-disable-line react-hooks/exhaustive-deps
  // /c/5 (enlace viejo por id) -> /c/<slug> en modo API
  useEffect(() => {
    if (snapResult) return; // ya lo maneja el efecto del snapshot
    if (mode === "category" && res?.category && cat !== res.category.slug)
      nav({ pathname: to(`/c/${res.category.slug}`), search: loc.search }, { replace: true });
  }, [res, cat, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadMore = () => {
    if (snapResult) {
      // snapshot: mostrar más sin llamada al API
      setDisplayLimit((n) => n + PAGE);
      return;
    }
    setMore(true);
    ctx.api<Res>(`/catalog?${qs}&offset=${items.length}`).then((r) => setItems((x) => [...x, ...r.items])).finally(() => setMore(false));
  };
  const avail = useAvailability(useMemo(() => items.map((p) => p.id), [items]));

  const set = useCallback((patch: Record<string, string | string[] | null>) => {
    const n = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) {
      n.delete(k);
      if (Array.isArray(v)) v.forEach((x) => n.append(k, x));
      else if (v) n.set(k, v);
    }
    setSp(n, { replace: true });
  }, [sp, setSp]);
  const clearAll = () => setSp(q ? new URLSearchParams({ q }) : new URLSearchParams(), { replace: true });
  const activeCount = brands.length + (tipo ? 1 : 0) + (min || max ? 1 : 0) + (disp ? 1 : 0);

  const title = mode === "search" ? (q ? `Resultados para “${q}”` : "Buscar") : res?.category?.name || (mode === "category" ? "Categoría" : "Catálogo completo");
  useHead({
    title: `${mode === "search" ? `Buscar “${q}”` : title} · ${ctx.store.name}`,
    description: mode === "category" && res?.category ? res.category.description || `${res.category.name} en ${ctx.store.name}: precios con IVA, envío a todo Costa Rica y asesoría técnica.` : "Catálogo completo con precios e IVA incluido.",
    path: mode === "category" && res?.category ? `/c/${res.category.slug}` : mode === "all" ? "/catalogo" : "/buscar",
    noindex: mode === "search" || activeCount > 0,
  });

  // hoja de filtros (movil)
  const [sheet, setSheet] = useState(false);
  const sheetRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!sheet) return;
    const prev = document.activeElement as HTMLElement | null;
    sheetRef.current?.querySelector<HTMLElement>("button, input")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setSheet(false);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [sheet]);

  if (missing) return <div className="sh-wrap"><EmptyHelp title="Esta categoría ya no está disponible." text="Puede que la hayamos reorganizado."><Link className="sh-btn" to={to("/catalogo")}>Ver catálogo completo</Link></EmptyHelp></div>;

  const crumbs: { name: string; path?: string }[] = [{ name: "Tienda", path: "/" }];
  if (res?.category?.parent) crumbs.push({ name: res.category.parent.name, path: res.category.parent.path });
  crumbs.push({ name: mode === "search" ? "Búsqueda" : title });

  const filters = res && <Filters f={res.facets} cur={res.currency} brands={brands} tipo={tipo} min={min} max={max} disp={disp} set={set} />;
  return (
    <div className="sh-wrap vt-catalog">
      <Crumbs items={crumbs} />
      <header className="vt-cathead">
        <h1>{title}</h1>
        {res?.category?.description && <p>{res.category.description}</p>}
      </header>
      <nav className="vt-catnav" aria-label="Categorías">
        <Link className="sh-chip" aria-current={mode === "all" ? "page" : undefined} data-on={mode === "all"} to={to("/catalogo")}>Todo</Link>
        {(res?.category?.children?.length ? res.category.children.map((c) => ({ key: c.path, name: c.name, path: c.path, n: undefined as number | undefined }))
          : (res?.nav || []).map((c) => ({ key: c.path, name: c.name, path: c.path, n: c.count as number | undefined }))).map((c) => {
          const on = mode === "category" && (res?.category?.name === c.name);
          return <Link key={c.key} className="sh-chip" data-on={on} aria-current={on ? "page" : undefined} to={to(c.path)}>{c.name}{c.n !== undefined && <i>{c.n}</i>}</Link>;
        })}
      </nav>
      <div className="vt-catalog__body">
        <aside className="vt-filters vt-hide-m" aria-label="Filtros">{filters || <div className="sh-skel" style={{ height: 320 }} />}</aside>
        <section aria-label="Productos" className="vt-results" aria-busy={loading} data-loading={loading && !!res}>
          <div className="vt-bar">
            <span className="sh-meta" aria-live="polite">{res ? `${res.total} resultado${res.total === 1 ? "" : "s"}` : "Cargando…"}</span>
            <div className="vt-bar__right">
              <button className="sh-btn sh-btn--ghost sh-btn--sm vt-show-m" onClick={() => setSheet(true)} aria-haspopup="dialog"><Ico d={IC.filter} />Filtros{activeCount ? ` (${activeCount})` : ""}</button>
              <label className="vt-sort"><span className="sh-meta vt-hide-m">Ordenar</span>
                <select className="sh-select" value={sort} onChange={(e) => set({ orden: e.target.value === "relevance" ? null : e.target.value })} aria-label="Ordenar por">
                  {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </label>
            </div>
          </div>
          {activeCount > 0 && (
            <div className="vt-active" aria-label="Filtros activos">
              {brands.map((b) => <button key={b} className="vt-tag" onClick={() => set({ marca: brands.filter((x) => x !== b) })}>{b}<Ico d={IC.x} size={14} /></button>)}
              {tipo && <button className="vt-tag" onClick={() => set({ tipo: null })}>{tipo}<Ico d={IC.x} size={14} /></button>}
              {(min || max) && <button className="vt-tag" onClick={() => set({ min: null, max: null })}>{min ? money(min, res?.currency) : "0"} – {max ? money(max, res?.currency) : "∞"}<Ico d={IC.x} size={14} /></button>}
              {disp && <button className="vt-tag" onClick={() => set({ disp: null })}>{disp === "inmediata" ? "Entrega inmediata" : "Disponible o bajo pedido"}<Ico d={IC.x} size={14} /></button>}
              <button className="vt-clear" onClick={clearAll}>Limpiar todo</button>
            </div>
          )}
          {!res ? <Skeleton /> : items.length === 0 ? (
            <EmptyHelp title={q ? `No encontramos “${q}”.` : "No hay productos con esos filtros."} text={q ? "Probá con el modelo (por ejemplo DS-2CD), la marca o una palabra más general. Si no lo tenemos publicado, lo conseguimos." : "Quitá algún filtro o mirá el catálogo completo."}>
              {activeCount > 0 ? <button className="sh-btn" onClick={clearAll}>Quitar filtros</button> : <Link className="sh-btn" to={to("/catalogo")}>Ver catálogo</Link>}
            </EmptyHelp>
          ) : (
            <>
              <Grid items={items} avail={avail} eager={4} />
              <div className="vt-pager">
                <span className="sh-meta">{items.length} de {res.total}</span>
                <div className="vt-pager__bar" aria-hidden="true"><i style={{ width: `${Math.min(100, (items.length / Math.max(1, res.total)) * 100)}%` }} /></div>
                {items.length < res.total && <button className="sh-btn sh-btn--ghost" onClick={loadMore} disabled={more}>{more ? "Cargando…" : `Ver ${Math.min(PAGE, res.total - items.length)} más`}</button>}
              </div>
            </>
          )}
        </section>
      </div>

      <div className="sh-scrim" data-open={sheet} onClick={() => setSheet(false)} />
      <div className="vt-sheet" data-open={sheet} role="dialog" aria-modal="true" aria-label="Filtros" aria-hidden={!sheet} ref={sheetRef}>
        <div className="vt-sheet__head"><b>Filtros</b><button className="sh-x" onClick={() => setSheet(false)} aria-label="Cerrar filtros"><Ico d={IC.x} /></button></div>
        <div className="vt-sheet__body">{filters}</div>
        <div className="vt-sheet__foot">
          <button className="sh-btn sh-btn--ghost" onClick={clearAll} disabled={!activeCount}>Limpiar</button>
          <button className="sh-btn sh-btn--crimson" onClick={() => setSheet(false)}>Ver {res?.total ?? ""} resultado{res?.total === 1 ? "" : "s"}</button>
        </div>
      </div>
    </div>
  );
}

function Filters({ f, cur, brands, tipo, min, max, disp, set }: { f: Facets; cur: string; brands: string[]; tipo: string; min: string; max: string; disp: string; set: (p: Record<string, string | string[] | null>) => void }) {
  const [lo, setLo] = useState(min);
  const [hi, setHi] = useState(max);
  useEffect(() => { setLo(min); setHi(max); }, [min, max]);
  const [allBrands, setAllBrands] = useState(false);
  const applyPrice = (e: FormEvent) => { e.preventDefault(); set({ min: lo.replace(/\D/g, "") || null, max: hi.replace(/\D/g, "") || null }); };
  const shownBrands = allBrands ? f.brands : f.brands.slice(0, 8);
  return (
    <div className="vt-fgroups">
      <fieldset className="vt-fgroup">
        <legend>Disponibilidad</legend>
        {([["", "Todo"], ["inmediata", `Entrega inmediata (${f.availability.disponible})`], ["bajo_pedido", `Disponible o bajo pedido (${f.availability.bajo_pedido})`]] as [string, string][]).map(([v, l]) => (
          <label key={v || "all"} className="vt-check"><input type="radio" name="disp" checked={disp === v} onChange={() => set({ disp: v || null })} /><span>{l}</span></label>
        ))}
      </fieldset>
      {f.types.length > 1 && (
        <fieldset className="vt-fgroup">
          <legend>Tipo de equipo</legend>
          <label className="vt-check"><input type="radio" name="tipo" checked={!tipo} onChange={() => set({ tipo: null })} /><span>Todos</span></label>
          {f.types.map((t) => <label key={t.name} className="vt-check"><input type="radio" name="tipo" checked={tipo === t.name} onChange={() => set({ tipo: t.name })} /><span>{t.name}</span><i>{t.count}</i></label>)}
        </fieldset>
      )}
      {f.brands.length > 0 && (
        <fieldset className="vt-fgroup">
          <legend>Marca</legend>
          {shownBrands.map((b) => (
            <label key={b.name} className="vt-check"><input type="checkbox" checked={brands.includes(b.name)} onChange={(e) => set({ marca: e.target.checked ? [...brands, b.name] : brands.filter((x) => x !== b.name) })} /><span>{b.name}</span><i>{b.count}</i></label>
          ))}
          {f.brands.length > 8 && <button type="button" className="vt-link" onClick={() => setAllBrands(!allBrands)}>{allBrands ? "Ver menos" : `Ver las ${f.brands.length} marcas`}</button>}
        </fieldset>
      )}
      <fieldset className="vt-fgroup">
        <legend>Precio (IVA incluido)</legend>
        <form className="vt-price-f" onSubmit={applyPrice}>
          <label><span className="sh-meta">Desde</span><input inputMode="numeric" value={lo} onChange={(e) => setLo(e.target.value)} placeholder={f.price.min ? money(f.price.min, cur) : "0"} aria-label="Precio mínimo" /></label>
          <label><span className="sh-meta">Hasta</span><input inputMode="numeric" value={hi} onChange={(e) => setHi(e.target.value)} placeholder={f.price.max ? money(f.price.max, cur) : ""} aria-label="Precio máximo" /></label>
          <button className="sh-btn sh-btn--sm" type="submit">Aplicar</button>
        </form>
      </fieldset>
    </div>
  );
}
