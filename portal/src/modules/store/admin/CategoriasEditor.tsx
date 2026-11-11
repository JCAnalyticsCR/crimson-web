/* Mi Tienda -> Categorias y colecciones (O4-07), con el guardado unico (O4-02).
   - Categorias: orden (subir y bajar, con teclado y tactil), ocultar de la tienda, imagen y texto para Google. El
     nombre NO se cambia aqui hasta que la Ola 3 (O3-07a) guarde la direccion con alias y redireccion 301: renombrar
     antes romperia los enlaces que Google ya tiene.
   - Colecciones por regla con dato real («Listo para entregar» = en bodega hoy, «por menos de ₡60 000», una
     categoria o un texto): se actualizan solas con la existencia. Su pagina publica /coleccion/<direccion> la arma la
     tienda (Ola 2); el API ya la sirve (404 real para una direccion que no existe). */
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../../../lib/api";
import { formatCRCInput, parseCRC } from "../../../lib/parseCRC";
import { Card, Field, I, Icon, Loading } from "../../../ui/components";
import { ImageField } from "../../../ui/MediaPicker";
import { SaveBar } from "./SaveBar";
import { useHub } from "./StoreHub";
import { moveItem } from "./fields";
import { useDraft } from "./useDraft";

type Cat = { id: number; name: string; parent_id: number | null; show_on_web: boolean };
type CatCfg = { position?: number | null; hidden?: boolean; image?: string; seo_text?: string };
type Rule = { stock_only: boolean; max_price: number | string | null; category_id: number | null; q: string };
type Coleccion = { slug: string; title: string; text: string; active: boolean; rule: Rule };
type Org = { categories: Record<string, CatCfg>; collections: Coleccion[] };
const LABELS: Record<string, string> = { categories: "Categorías", collections: "Colecciones" };
const slugify = (s: string) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
const EJEMPLOS: Coleccion[] = [
  { slug: "listo-para-entregar", title: "Listo para entregar", text: "En bodega hoy.", active: true, rule: { stock_only: true, max_price: null, category_id: null, q: "" } },
  { slug: "camaras-para-casa", title: "Cámaras para casa por menos de ₡60 000", text: "", active: true, rule: { stock_only: false, max_price: 60000, category_id: null, q: "camara" } },
];

export default function CategoriasEditor() {
  const { cfg } = useHub();
  const [cats, setCats] = useState<Cat[] | null>(null);
  const [cuantos, setCuantos] = useState<Record<string, number | null>>({});
  useEffect(() => { api<Cat[]>("/categories").then(setCats).catch(() => setCats([])); }, []);
  const d = useDraft<Org>({
    key: "categorias:showcase",
    url: "/store/showcase",
    read: (r) => ({ categories: (r.categories as Record<string, CatCfg>) || {}, collections: ((r.collections as Coleccion[]) || []).map((c) => ({ ...c, text: c.text || "", rule: { stock_only: !!c.rule?.stock_only, max_price: c.rule?.max_price ?? null, category_id: c.rule?.category_id ?? null, q: c.rule?.q || "" } })) }),
    body: (x, changed, version) => ({ ...Object.fromEntries(changed.map((k) => [k, k === "collections" ? x.collections.map((c) => ({ ...c, rule: { ...c.rule, max_price: c.rule.max_price === "" || c.rule.max_price == null ? null : Number(c.rule.max_price) } })) : x[k]])), version }),
  });
  // cuantos productos tiene hoy cada coleccion guardada (la misma regla del API)
  useEffect(() => {
    if (!cfg || !d.server) return;
    d.server.collections.forEach((c) => {
      fetch(`/api/public/store/${cfg.slug}/collections/${c.slug}`).then((r) => (r.ok ? r.json() : null)).then((j) => setCuantos((x) => ({ ...x, [c.slug]: j ? j.n : null }))).catch(() => {});
    });
  }, [cfg, d.server]);
  if (d.error) return <Card title="Categorías"><p className="mt-lead">{d.error}</p></Card>;
  if (!d.draft || !cats) return <Loading />;
  const org = d.draft;
  const adj = (id: number): CatCfg => org.categories[String(id)] || {};
  const setAdj = (id: number, p: Partial<CatCfg>) => d.set({ categories: { ...org.categories, [String(id)]: { ...adj(id), ...p } } });
  const hijos = (pid: number | null) => cats.filter((c) => (c.parent_id ?? null) === pid).sort((a, b) => (adj(a.id).position ?? 10_000) - (adj(b.id).position ?? 10_000) || a.name.localeCompare(b.name));
  const mover = (pid: number | null, i: number, dir: -1 | 1) => {
    const lista = moveItem(hijos(pid), i, dir);
    const next = { ...org.categories };
    lista.forEach((c, k) => { next[String(c.id)] = { ...(next[String(c.id)] || {}), position: k }; });
    d.set({ categories: next });
  };
  const setCol = (i: number, p: Partial<Coleccion>) => d.set({ collections: org.collections.map((c, k) => (k === i ? { ...c, ...p } : c)) });
  const setRule = (i: number, p: Partial<Rule>) => setCol(i, { rule: { ...org.collections[i].rule, ...p } });
  const validar = () => {
    const slugs = org.collections.map((c) => c.slug);
    if (org.collections.some((c) => !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(c.slug) || c.title.trim().length < 2)) return "Cada colección necesita un título y una dirección (minúsculas, números y guiones).";
    if (new Set(slugs).size !== slugs.length) return "Dos colecciones no pueden tener la misma dirección.";
    return null;
  };

  // funcion (no componente): un componente definido aqui adentro se volveria a montar en cada tecla
  const rama = (pid: number | null, nivel: number): ReactNode => {
    const lista = hijos(pid);
    if (!lista.length) return null;
    return (
      <ul className="ct-list" style={{ paddingLeft: nivel ? 18 : 0 }}>
        {lista.map((c, i) => {
          const a = adj(c.id);
          return (
            <li key={c.id} className="mt-box">
              <div className="mt-row" style={{ justifyContent: "space-between" }}>
                <b style={{ fontSize: 13.5 }}>{c.name}{a.hidden && <span className="badge badge--muted" style={{ marginLeft: 6 }}>oculta</span>}</b>
                <span className="mt-row">
                  <button className="btn btn--ghost btn--sm" onClick={() => mover(pid, i, -1)} disabled={i === 0} aria-label={`Subir ${c.name}`}>↑</button>
                  <button className="btn btn--ghost btn--sm" onClick={() => mover(pid, i, 1)} disabled={i === lista.length - 1} aria-label={`Bajar ${c.name}`}>↓</button>
                </span>
              </div>
              <details>
                <summary className="meta" style={{ cursor: "pointer", textTransform: "none", minHeight: 32 }}>Imagen, texto y visibilidad</summary>
                <div className="mt-stack" style={{ marginTop: 8 }}>
                  <label className="pw-check"><input type="checkbox" checked={!!a.hidden} onChange={(e) => setAdj(c.id, { hidden: e.target.checked })} />Ocultar del menú y de la portada (los productos siguen en el catálogo)</label>
                  <Field label="Imagen de la categoría" hint="Vacía = la foto de un producto de la categoría."><ImageField value={a.image || null} label={`Imagen de ${c.name}`} onChange={(url) => setAdj(c.id, { image: url || "" })} /></Field>
                  <Field label="Texto para Google" hint="Dos o tres frases sobre qué encuentra el cliente en esta categoría."><textarea className="textarea" rows={3} maxLength={600} value={a.seo_text || ""} onChange={(e) => setAdj(c.id, { seo_text: e.target.value })} /></Field>
                </div>
              </details>
              {rama(c.id, nivel + 1)}
            </li>
          );
        })}
      </ul>
    );
  };

  return (
    <>
      <Card title="Categorías">
        <div className="mt-stack">
          <p className="mt-lead">El orden es el del menú y la portada de la tienda. Para cambiar el nombre de una categoría hay que esperar a que la tienda guarde la dirección vieja con redirección (Ola 3): así no se pierden los enlaces que Google ya tiene.</p>
          {cats.length === 0 ? <p className="mt-lead">Todavía no hay categorías: se crean en Productos y servicios.</p> : rama(null, 0)}
        </div>
      </Card>
      <Card title="Colecciones" extra={<button className="btn btn--ghost btn--sm" onClick={() => d.set({ collections: [...org.collections, { slug: "", title: "", text: "", active: true, rule: { stock_only: false, max_price: null, category_id: null, q: "" } }] })} disabled={org.collections.length >= 20}><Icon d={I.plus} />Colección</button>}>
        <div className="mt-stack">
          <p className="mt-lead">Una colección junta productos por una regla con dato real y se actualiza sola. Ejemplos: {EJEMPLOS.map((e, k) => <button key={k} className="btn btn--ghost btn--sm" style={{ marginLeft: 6 }} disabled={org.collections.some((c) => c.slug === e.slug)} onClick={() => d.set({ collections: [...org.collections, e] })}>{e.title}</button>)}</p>
          {org.collections.map((c, i) => (
            <div key={i} className="mt-box">
              <div className="grid-2">
                <Field label="Título"><input className="input" value={c.title} maxLength={80} onChange={(e) => setCol(i, { title: e.target.value, slug: c.slug || slugify(e.target.value) })} /></Field>
                <Field label="Dirección" hint={`/coleccion/${c.slug || "…"}`}><input className="input input--mono" style={{ textAlign: "left" }} value={c.slug} maxLength={60} onChange={(e) => setCol(i, { slug: slugify(e.target.value) })} /></Field>
                <Field label="Precio máximo con IVA (₡)" hint="Vacío = sin límite."><input className="input input--mono" inputMode="decimal" value={c.rule.max_price == null || c.rule.max_price === "" ? "" : formatCRCInput(Number(c.rule.max_price))} onChange={(e) => { const r = parseCRC(e.target.value); if (!e.target.value.trim()) setRule(i, { max_price: null }); else if (r.ok) setRule(i, { max_price: r.value }); }} /></Field>
                <Field label="Categoría"><select className="select" value={c.rule.category_id ?? ""} onChange={(e) => setRule(i, { category_id: e.target.value ? Number(e.target.value) : null })}><option value="">Todas</option>{cats.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}</select></Field>
                <Field label="Texto que debe tener" hint="Por ejemplo «wifi»."><input className="input" value={c.rule.q} maxLength={80} onChange={(e) => setRule(i, { q: e.target.value })} /></Field>
                <Field label="Texto de la colección"><input className="input" value={c.text} maxLength={300} onChange={(e) => setCol(i, { text: e.target.value })} /></Field>
              </div>
              <div className="mt-row" style={{ justifyContent: "space-between" }}>
                <span className="mt-row">
                  <label className="pw-check"><input type="checkbox" checked={c.rule.stock_only} onChange={(e) => setRule(i, { stock_only: e.target.checked })} />Solo lo que está en bodega hoy</label>
                  <label className="pw-check"><input type="checkbox" checked={c.active} onChange={(e) => setCol(i, { active: e.target.checked })} />Activa</label>
                </span>
                <span className="mt-row">
                  {c.slug in cuantos && <span className="meta" style={{ textTransform: "none" }}>{cuantos[c.slug] == null ? "Sin publicar" : `Hoy: ${cuantos[c.slug]} productos`}</span>}
                  <button className="btn btn--danger btn--sm" onClick={() => d.set({ collections: org.collections.filter((_, k) => k !== i) })}>Quitar</button>
                </span>
              </div>
            </div>
          ))}
        </div>
      </Card>
      <SaveBar drafts={[d]} labels={LABELS} validate={validar} />
    </>
  );
}
