/* Mi Tienda -> Catalogo (O4-04): todo el catalogo web en un lugar. Paginado en el servidor (48 por pagina, todos
   los publicados alcanzables), con busqueda, categoria, marca, publicado, estado que ve el cliente y fichas de
   calidad con su numero («Sin descripción 823»). Cuadricula de tarjetas o tabla (apilada en tablet). Clic en una
   tarjeta abre el cajon /mi-tienda/catalogo/:id con todo lo web del producto. Los filtros viven en la direccion
   (?falta=sin_descripcion...): el Resumen lleva aqui ya filtrado y recargar no los pierde. */
import { useCallback, useEffect, useState } from "react";
import { Link, Route, Routes, useLocation, useSearchParams } from "react-router-dom";
import { api } from "../../../lib/api";
import { Card, Empty, Loading } from "../../../ui/components";
import { money } from "../storeKit";
import ProductoWebDrawer from "./ProductoWebDrawer";
import { BulkBar } from "./BulkBar";

export type Item = {
  id: number; name: string; code: string; brand: string | null; category: string | null; image: string | null; photos: number;
  show_on_web: boolean; web_featured: boolean; state: string | null; label: string | null; accion: string | null; faltas: string[];
  display_price: number | string | null; currency: string; path: string;
};
export type Listing = {
  items: Item[]; total: number; page: number; pages: number; per_page: number;
  faltas: { key: string; label: string; n: number }[]; estados: { key: string; label: string; n: number }[];
  brands: string[]; categories: { id: number; name: string }[];
};
const FALTA_CORTA: Record<string, string> = { sin_descripcion: "Sin descripción", una_foto: "Una foto", sin_foto: "Sin foto", sin_ficha: "Sin ficha", nombre_largo: "Nombre largo", foto_proveedor: "Foto del proveedor", por_confirmar: "Por confirmar", en_oferta: "En oferta" };
const PARAMS = ["q", "categoria", "marca", "estado", "publicado", "falta", "pagina"] as const;

export function useCatalogQuery() {
  const [sp, setSp] = useSearchParams();
  const set = (patch: Partial<Record<(typeof PARAMS)[number] | "vista", string | null>>) => {
    const n = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) { if (v) n.set(k, v); else n.delete(k); }
    if (!("pagina" in patch)) n.delete("pagina");
    setSp(n, { replace: true });
  };
  const qs = new URLSearchParams(PARAMS.flatMap((k) => (sp.get(k) ? [[k, sp.get(k)!]] : [])));
  return { sp, set, qs: qs.toString() };
}

export default function CatalogoWeb() {
  const { sp, set, qs } = useCatalogQuery();
  const loc = useLocation();
  const [l, setL] = useState<Listing | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState(sp.get("q") || "");
  const vista = sp.get("vista") === "tabla" ? "tabla" : "cuadricula";
  const load = useCallback(() => { api<Listing>(`/store/catalog${qs ? `?${qs}` : ""}`).then((r) => { setL(r); setErr(null); }).catch((e) => setErr(e.message)); }, [qs]);
  useEffect(load, [load]);
  useEffect(() => { const t = window.setTimeout(() => { if ((sp.get("q") || "") !== q.trim()) set({ q: q.trim() || null }); }, 300); return () => window.clearTimeout(t); }, [q]); // eslint-disable-line react-hooks/exhaustive-deps
  const falta = sp.get("falta");
  const estado = sp.get("estado");
  const abrir = (id: number) => `/mi-tienda/catalogo/${id}${loc.search}`;
  /* O4-05: seleccion para la edicion en lote (se limpia al cambiar los filtros); `todos` = los N del filtro */
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [todos, setTodos] = useState(false);
  useEffect(() => { setSel(new Set()); setTodos(false); }, [qs]);
  const toggle = (id: number) => { setTodos(false); setSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; }); };
  const selectable = { selected: sel, toggle };
  const pagina = l?.items.map((x) => x.id) || [];
  const todaLaPagina = pagina.length > 0 && pagina.every((id) => sel.has(id));
  const filtro = Object.fromEntries(["q", "categoria", "marca", "estado", "publicado", "falta"].flatMap((k) => (sp.get(k) ? [[k, sp.get(k)!]] : [])));

  return (
    <>
      <Card title="Catálogo de la tienda" extra={l && <span className="meta" style={{ textTransform: "none" }}>{l.total.toLocaleString("es-CR")} productos</span>}>
        <div className="mt-stack">
          <div className="cw-filters">
            <input className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar por nombre, código o marca" aria-label="Buscar en el catálogo" />
            <select className="select" value={sp.get("categoria") || ""} onChange={(e) => set({ categoria: e.target.value || null })} aria-label="Categoría"><option value="">Todas las categorías</option>{l?.categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
            <select className="select" value={sp.get("marca") || ""} onChange={(e) => set({ marca: e.target.value || null })} aria-label="Marca"><option value="">Todas las marcas</option>{l?.brands.map((b) => <option key={b}>{b}</option>)}</select>
            <select className="select" value={sp.get("publicado") || "si"} onChange={(e) => set({ publicado: e.target.value === "si" ? null : e.target.value })} aria-label="Publicado"><option value="si">Publicados</option><option value="no">No publicados</option><option value="todos">Todos</option></select>
          </div>
          {l && (
            <div className="cw-chips" role="group" aria-label="Filtros de calidad">
              {l.estados.filter((e) => e.key !== "servicio" || e.n).map((e) => (
                <button key={e.key} className={`cw-chip${estado === e.key ? " is-on" : ""}`} aria-pressed={estado === e.key} onClick={() => set({ estado: estado === e.key ? null : e.key })}>{e.label} <b>{e.n.toLocaleString("es-CR")}</b></button>
              ))}
              {l.faltas.map((f) => (
                <button key={f.key} className={`cw-chip${falta === f.key ? " is-on" : ""}`} aria-pressed={falta === f.key} onClick={() => set({ falta: falta === f.key ? null : f.key })}>{f.label} <b>{f.n.toLocaleString("es-CR")}</b></button>
              ))}
            </div>
          )}
          <div className="mt-row" style={{ justifyContent: "space-between" }}>
            {l && l.items.length > 0 ? <label className="pw-check"><input type="checkbox" checked={todaLaPagina} onChange={() => { setTodos(false); setSel(todaLaPagina ? new Set() : new Set([...sel, ...pagina])); }} />Seleccionar esta página</label> : <span />}
            <div className="mt-row" role="group" aria-label="Vista">
              <button className={`btn btn--sm ${vista === "cuadricula" ? "btn--soft" : "btn--ghost"}`} aria-pressed={vista === "cuadricula"} onClick={() => set({ vista: null, pagina: sp.get("pagina") })}>Cuadrícula</button>
              <button className={`btn btn--sm ${vista === "tabla" ? "btn--soft" : "btn--ghost"}`} aria-pressed={vista === "tabla"} onClick={() => set({ vista: "tabla", pagina: sp.get("pagina") })}>Tabla</button>
            </div>
          </div>
          {l && <BulkBar count={sel.size} total={l.total} all={todos} filtro={filtro} ids={[...sel]} categories={l.categories} onAll={() => setTodos(true)} onClear={() => { setSel(new Set()); setTodos(false); }} onDone={load} />}
          {err ? <p className="mt-lead">{err}</p> : !l ? <Loading /> : l.items.length === 0 ? <Empty title="Nada con esos filtros" hint="Quite un filtro o busque otra cosa." /> : vista === "tabla" ? (
            <table className="table table--stack cw-table">
              <thead><tr>{selectable && <th />}<th>Producto</th><th>Estado</th><th className="num">Precio</th><th>Le falta</th></tr></thead>
              <tbody>
                {l.items.map((x) => (
                  <tr key={x.id}>
                    {selectable && <td><input type="checkbox" checked={selectable.selected.has(x.id)} onChange={() => selectable.toggle(x.id)} aria-label={`Seleccionar ${x.name}`} /></td>}
                    <td data-label="Producto"><Link className="row-link" to={abrir(x.id)}>{x.name}</Link><div className="meta" style={{ textTransform: "none" }}>{x.code}{x.brand ? ` · ${x.brand}` : ""}{!x.show_on_web ? " · no publicado" : ""}</div></td>
                    <td data-label="Estado">{x.label || "—"}</td>
                    <td data-label="Precio" className="num">{x.display_price != null ? money(x.display_price, x.currency) : "—"}</td>
                    <td data-label="Le falta">{x.faltas.map((f) => FALTA_CORTA[f]).join(", ") || "Nada"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <ul className="cw-grid">
              {l.items.map((x) => (
                <li key={x.id} className={`cw-card${selectable?.selected.has(x.id) ? " is-sel" : ""}`}>
                  {selectable && <input className="cw-card__sel" type="checkbox" checked={selectable.selected.has(x.id)} onChange={() => selectable.toggle(x.id)} aria-label={`Seleccionar ${x.name}`} />}
                  <Link to={abrir(x.id)} className="cw-card__link">
                    <span className="cw-card__img">{x.image ? <img src={x.image} alt="" loading="lazy" width={160} height={160} /> : <span className="meta">Sin foto</span>}</span>
                    <span className="cw-card__name">{x.name}</span>
                    <span className="meta" style={{ textTransform: "none" }}>{x.code}{x.brand ? ` · ${x.brand}` : ""}</span>
                    <span className="cw-card__row">
                      <span className={`badge ${x.accion === "comprar" ? "badge--ok" : "badge--warn"}`}>{x.label || "Sin dato"}</span>
                      {!x.show_on_web && <span className="badge badge--muted">No publicado</span>}
                      {x.web_featured && <span className="badge badge--info">Fijado</span>}
                    </span>
                    {x.display_price != null && <span className="cw-card__price">{money(x.display_price, x.currency)} <small>IVA incl.</small></span>}
                    {x.faltas.length > 0 && <span className="cw-card__faltas">Le falta: {x.faltas.map((f) => FALTA_CORTA[f]).join(", ")}</span>}
                  </Link>
                </li>
              ))}
            </ul>
          )}
          {l && l.pages > 1 && (
            <nav className="mt-row cw-pages" aria-label="Páginas del catálogo">
              <button className="btn btn--ghost btn--sm" disabled={l.page <= 1} onClick={() => set({ pagina: String(l.page - 1) })}>Anterior</button>
              <span className="meta" style={{ textTransform: "none" }}>Página {l.page} de {l.pages}</span>
              <button className="btn btn--ghost btn--sm" disabled={l.page >= l.pages} onClick={() => set({ pagina: String(l.page + 1) })}>Siguiente</button>
            </nav>
          )}
        </div>
      </Card>
      <Routes>
        <Route path=":id" element={<ProductoWebDrawer onSaved={load} />} />
      </Routes>
    </>
  );
}
