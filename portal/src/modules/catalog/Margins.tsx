/* Catálogo → Márgenes (10/11): el margen sobre la venta por categoría.
   Prioridad: margen propio del producto → categoría (la más cercana, subiendo por las padres) → general (Ajustes).
   Conservar precios: guardar un margen NUNCA reprecia solo. Primero la vista previa (cuántos productos cambian y su
   precio antes y después); los precios cambian solo al confirmar esa misma vista previa (el API trae una huella y, si
   algo cambió entretanto, la rechaza). Quitar el margen de una categoría no cambia precios. Solo quien ve costos. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Loading, Modal } from "../../ui/components";
import "./productos.css";

type Num = number | string;
type CatRow = {
  id: number; name: string; path: string; parent_id: number | null; margin_pct: Num | null; effective_pct: Num;
  source: "propio" | "heredado" | "general"; source_name: string | null; products: number; products_tree: number; own_margin: number; sin_costo: number;
};
type Overview = { general_pct: Num; categories: CatRow[]; uncategorized: number };
type ProdRow = {
  id: number; code: string; name: string; category: string; currency: string; price: Num; cost_crc: Num | null; margin_real: Num | null;
  margin_pct: Num; source: "producto" | "categoria" | "general"; source_name: string | null; show_on_web: boolean;
};
type PrevRow = {
  id: number; code: string; name: string; category: string; currency: string; price_before: Num; price_after: Num | null; margin_before: Num | null;
  margin_after: Num | null; own_margin: Num | null; clears_own: boolean; web_price: Num | null; show_on_web: boolean; skip: string | null;
};
type Preview = {
  category: { id: number; name: string; path: string; margin_pct: Num | null }; margin_pct: Num; include_own: boolean; total: number;
  counts: { cambian: number; sin_cambio: number; sin_costo: number; margen_propio: number; por_subcategoria: number; sin_tasa: number };
  own_clears: number; below_min: boolean; min_margin_pct: Num; items: PrevRow[]; token: string;
};

const pct = (v: Num | null | undefined) => (v == null ? "—" : `${Number(v).toLocaleString("es-CR", { maximumFractionDigits: 2 })} %`);
const tone = (m: number) => (m < 20 ? "var(--bad)" : m < 30 ? "var(--warn)" : "var(--ok)");
const plural = (n: number, uno: string, varios: string) => `${n.toLocaleString("es-CR")} ${n === 1 ? uno : varios}`;
const SRC: Record<string, string> = { producto: "Propio del producto", categoria: "De la categoría", general: "General" };

export default function Margins() {
  const { allows } = useSession();
  const edita = allows("catalog.editar");
  const [data, setData] = useState<Overview | null>(null);
  const [q, setQ] = useState("");
  const [soloConMargen, setSoloConMargen] = useState(false);
  const [ver, setVer] = useState<CatRow | null>(null);
  const [definir, setDefinir] = useState<CatRow | null>(null);

  const load = useCallback(() => api<Overview>("/catalog/margins").then(setData), []);
  useEffect(() => { load(); }, [load]);

  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    return (data?.categories || []).filter((c) => (!t || c.path.toLowerCase().includes(t)) && (!soloConMargen || c.margin_pct != null));
  }, [data, q, soloConMargen]);

  return (
    <>
      <div className="page-head">
        <div><div className="meta">05 · Catálogo</div><h1 className="h1">Márgenes por categoría</h1></div>
        <div className="page-head__actions"><Link className="btn btn--ghost" to="/productos"><Icon d={I.products} />Productos</Link></div>
      </div>
      <Card flush>
        <div className="list-head" style={{ flexWrap: "wrap" }}>
          <div className="search" style={{ maxWidth: 340 }}><Icon d={I.search} size={16} /><input placeholder="Buscar categoría…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar categoría" /></div>
          <button className={`btn btn--sm ${soloConMargen ? "btn--crimson" : "btn--ghost"}`} onClick={() => setSoloConMargen(!soloConMargen)}>Solo con margen propio</button>
          <span className="muted" style={{ fontSize: 13, marginLeft: "auto" }}>Margen general: <b className="mono">{data ? pct(data.general_pct) : "…"}</b> · <Link to="/ajustes">Ajustes</Link></span>
        </div>
        <p className="muted mrg-help">Cada producto usa su <b>margen propio</b>; si no tiene, el de su <b>categoría</b> (o la categoría padre más cercana con margen); si ninguna tiene, el <b>general</b>. Definir el margen de una categoría muestra primero cómo quedan los precios y solo los cambia al confirmar.</p>
        {data === null ? <Loading /> : rows.length === 0 ? <Empty hint={q || soloConMargen ? "Ninguna categoría con ese filtro." : "Todavía no hay categorías."} /> : (
          <table className="table table--stack mrg-table">
            <thead><tr><th>Categoría</th><th className="num">Margen propio</th><th>Margen que rige</th><th className="num">Productos</th><th className="num">Con margen propio</th><th className="num">Sin costo</th><th /></tr></thead>
            <tbody>{rows.map((c) => (
              <tr key={c.id} className={ver?.id === c.id ? "is-sel" : undefined}>
                <td data-label="Categoría" className="stack-full"><b>{c.name}</b>{c.path !== c.name && <div className="muted" style={{ fontSize: 12 }}>{c.path}</div>}</td>
                <td data-label="Margen propio" className="num mono" style={{ fontWeight: 700 }}>{c.margin_pct != null ? pct(c.margin_pct) : <span className="muted">—</span>}</td>
                <td data-label="Margen que rige"><span className="mono">{pct(c.effective_pct)}</span> <span className={`badge ${c.source === "propio" ? "badge--ok" : c.source === "heredado" ? "badge--info" : "badge--muted"}`}>{c.source === "propio" ? "propio" : c.source === "heredado" ? `de ${c.source_name}` : "general"}</span></td>
                <td data-label="Productos" className="num mono" title={`${c.products} directos, ${c.products_tree} con subcategorías`}>{c.products_tree}</td>
                <td data-label="Con margen propio" className="num mono">{c.own_margin || <span className="muted">0</span>}</td>
                <td data-label="Sin costo" className="num mono">{c.sin_costo ? <span style={{ color: "var(--warn)" }}>{c.sin_costo}</span> : <span className="muted">0</span>}</td>
                <td className="stack-actions mrg-acc">
                  <button className="btn btn--ghost btn--sm" onClick={() => setVer(ver?.id === c.id ? null : c)} aria-expanded={ver?.id === c.id}>{ver?.id === c.id ? "Ocultar" : "Ver productos"}</button>
                  {edita && <button className="btn btn--soft btn--sm" onClick={() => setDefinir(c)}>Definir margen</button>}
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {!!data?.uncategorized && <p className="muted mrg-help">{plural(data.uncategorized, "producto sin categoría usa", "productos sin categoría usan")} el margen general o el propio.</p>}
      </Card>
      {ver && <Productos cat={ver} onClose={() => setVer(null)} />}
      {definir && <DefinirMargen cat={definir} general={data?.general_pct ?? 35} onClose={() => setDefinir(null)} onDone={() => { setDefinir(null); load(); setVer((v) => (v ? { ...v } : v)); }} />}
    </>
  );
}

function Productos({ cat, onClose }: { cat: CatRow; onClose: () => void }) {
  const [items, setItems] = useState<ProdRow[] | null>(null);
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement>(null);
  /* en tablet y teléfono la lista queda bajo todas las categorías: se la trae a la vista al abrirla */
  useEffect(() => { box.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [cat.id]);
  useEffect(() => {
    setItems(null);
    const t = setTimeout(() => {
      const qs = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : "";
      api<{ items: ProdRow[] }>(`/catalog/margins/${cat.id}/products${qs}`).then((r) => setItems(r.items)).catch(() => setItems([]));
    }, 200);
    return () => clearTimeout(t);
  }, [cat, q]);
  return (
    <div ref={box} style={{ scrollMarginTop: 16 }}>
    <Card flush title={<>Productos de {cat.name} <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>(con subcategorías)</span></>} extra={<button className="btn btn--ghost btn--sm" onClick={onClose} aria-label="Cerrar"><Icon d={I.x} size={14} /></button>}>
      <div className="list-head"><div className="search" style={{ maxWidth: 340 }}><Icon d={I.search} size={16} /><input placeholder="Nombre, código o modelo…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar producto" /></div></div>
      {items === null ? <Loading /> : items.length === 0 ? <Empty hint="Sin productos activos en esta categoría." /> : (
        <table className="table table--sm table--stack">
          <thead><tr><th>Producto</th><th>Categoría</th><th className="num">Precio sin IVA</th><th className="num">Costo ₡</th><th className="num">Margen real</th><th>Margen que rige</th></tr></thead>
          <tbody>{items.map((p) => (
            <tr key={p.id}>
              <td data-label="Producto" className="stack-full"><Link to={`/productos?q=${encodeURIComponent(p.code)}`}><b>{p.name}</b></Link> <span className="muted mono" style={{ fontSize: 12 }}>{p.code}</span>{!p.show_on_web && <span className="badge badge--muted" style={{ marginLeft: 6 }}>oculto</span>}</td>
              <td data-label="Categoría" className="muted">{p.category}</td>
              <td data-label="Precio sin IVA" className="num mono">{fmtMoney(p.price, p.currency)}</td>
              <td data-label="Costo ₡" className="num mono">{p.cost_crc != null ? fmtMoney(p.cost_crc, "CRC") : <span className="muted">sin costo</span>}</td>
              <td data-label="Margen real" className="num mono" style={p.margin_real != null ? { color: tone(Number(p.margin_real)), fontWeight: 700 } : undefined}>{pct(p.margin_real)}</td>
              <td data-label="Margen que rige"><span className="mono">{pct(p.margin_pct)}</span> <span className="muted" style={{ fontSize: 12 }}>{SRC[p.source]}{p.source === "categoria" && p.source_name ? ` (${p.source_name})` : ""}</span></td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </Card>
    </div>
  );
}

function DefinirMargen({ cat, general, onClose, onDone }: { cat: CatRow; general: Num; onClose: () => void; onDone: () => void }) {
  const { toast } = useSession();
  const [value, setValue] = useState(String(Number(cat.margin_pct ?? cat.effective_pct ?? general)));
  const [own, setOwn] = useState(false);
  const [prev, setPrev] = useState<Preview | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const num = Number(value.replace(",", "."));
  const valido = Number.isFinite(num) && num > 0 && num < 95;
  const reset = () => { setPrev(null); setConfirming(false); };

  const preview = async () => {
    if (!valido) { toast("El margen va de 0 % a 95 %", "bad"); return; }
    setBusy(true);
    try { setPrev(await api<Preview>(`/catalog/margins/${cat.id}/preview`, { method: "POST", json: { margin_pct: num, include_own: own } })); setConfirming(false); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  const apply = async () => {
    if (!prev) return;
    setBusy(true);
    try {
      const r = await api<{ changes: number; own_cleared: number }>(`/catalog/margins/${cat.id}/apply`, { method: "POST", json: { margin_pct: num, include_own: own, token: prev.token } });
      toast(r.changes ? `Margen de ${cat.name}: ${pct(num)} · ${plural(r.changes, "precio actualizado", "precios actualizados")}` : `Margen de ${cat.name} guardado: ${pct(num)}`);
      onDone();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Error", "bad");
      // 409: algo cambio desde la vista previa -> se la mostramos de nuevo
      if (e instanceof Error && /vista previa/i.test(e.message)) preview();
    } finally { setBusy(false); }
  };
  const quitar = async () => {
    setBusy(true);
    try { await api(`/catalog/margins/${cat.id}`, { method: "DELETE" }); toast(`${cat.name} vuelve a usar el margen ${cat.parent_id ? "de la categoría padre o el general" : "general"}. Ningún precio cambió.`); onDone(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  const n = prev?.counts.cambian ?? 0;
  return (
    <Modal title={`Margen de ${cat.name}`} onClose={onClose} wide dirty={!!prev} foot={<>
      {cat.margin_pct != null && !prev && <button className="btn btn--ghost" style={{ marginRight: "auto" }} disabled={busy} onClick={quitar} title="Vuelve a regir el de la categoría padre o el general. No cambia precios.">Quitar margen</button>}
      <button className="btn btn--ghost" onClick={onClose}>Cancelar</button>
      <button className="btn btn--soft" disabled={busy || !valido} onClick={preview}>{prev ? "Actualizar vista previa" : "Vista previa"}</button>
      {prev && !confirming && <button className="btn btn--crimson" disabled={busy} onClick={() => setConfirming(true)}>{n ? `Aplicar a ${plural(n, "producto", "productos")}` : "Guardar margen"}</button>}
      {prev && confirming && <button className="btn btn--crimson" disabled={busy} onClick={apply}>{n ? `Sí, cambiar ${plural(n, "precio", "precios")}` : "Sí, guardar"}</button>}
    </>}>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
        {cat.path} · {plural(cat.products_tree, "producto activo", "productos activos")} con sus subcategorías. Rige hoy: <b>{pct(cat.effective_pct)}</b> ({cat.source === "propio" ? "propio" : cat.source === "heredado" ? `de ${cat.source_name}` : "general"}).
      </p>
      <div className="grid-2">
        <Field label="Margen sobre la venta %" hint="precio = costo ÷ (1 − margen), en colones redondeado a la centena hacia arriba. Sin costo no se toca.">
          <input className="input input--mono" inputMode="decimal" value={value} onChange={(e) => { setValue(e.target.value); reset(); }} aria-label="Margen sobre la venta en porcentaje" />
        </Field>
        {cat.own_margin > 0 && (
          <Field label="Productos con margen propio" hint={`${plural(cat.own_margin, "producto tiene", "productos tienen")} su propio margen. Por defecto se respetan.`}>
            <label className="mrg-check"><input type="checkbox" checked={own} onChange={(e) => { setOwn(e.target.checked); reset(); }} /> Incluirlos también (dejan su margen propio y usan el de la categoría)</label>
          </Field>
        )}
      </div>
      {prev && (
        <>
          <div className="mrg-resumen" role="status">
            <div><b className="mrg-big">{n}</b> <span>de {plural(prev.total, "producto", "productos")} cambian de precio</span></div>
            <ul>
              {prev.counts.sin_cambio > 0 && <li>{plural(prev.counts.sin_cambio, "ya tiene", "ya tienen")} ese precio</li>}
              {prev.counts.margen_propio > 0 && <li>{plural(prev.counts.margen_propio, "con margen propio se respeta", "con margen propio se respetan")}</li>}
              {prev.counts.por_subcategoria > 0 && <li>{plural(prev.counts.por_subcategoria, "usa", "usan")} el margen de su subcategoría</li>}
              {prev.counts.sin_costo > 0 && <li>{plural(prev.counts.sin_costo, "sin costo no cambia", "sin costo no cambian")}</li>}
              {prev.counts.sin_tasa > 0 && <li>{plural(prev.counts.sin_tasa, "sin tipo de cambio", "sin tipo de cambio")}</li>}
              {prev.own_clears > 0 && <li>{plural(prev.own_clears, "deja", "dejan")} su margen propio</li>}
            </ul>
            {prev.below_min && <p className="mrg-warn">Ese margen está bajo el mínimo de {pct(prev.min_margin_pct)} (Ajustes): esos productos pueden quedar en «Precios por revisar».</p>}
            {prev.items.some((r) => r.price_after != null && r.web_price != null) && <p className="mrg-warn">Algunos tienen <b>precio web propio</b>: la tienda sigue mostrando ese precio hasta que lo quite en la ficha.</p>}
          </div>
          {confirming && <p className="mrg-confirm">{n ? <>Va a cambiar el precio de <b>{plural(n, "producto", "productos")}</b> de {cat.name} con un margen de <b>{pct(num)}</b>. El cambio queda en la bitácora.</> : <>No cambia ningún precio; se guarda el margen de {pct(num)} para {cat.name}.</>} ¿Confirma?</p>}
          <div className="mrg-prev">
            <table className="table table--sm table--stack">
              <thead><tr><th>Producto</th><th className="num">Precio actual</th><th className="num">Precio nuevo</th><th className="num">Margen actual</th><th className="num">Margen nuevo</th></tr></thead>
              <tbody>{[...prev.items].sort((a, b) => Number(b.price_after != null) - Number(a.price_after != null)).map((r) => (
                <tr key={r.id} style={r.price_after == null ? { opacity: 0.6 } : undefined}>
                  <td data-label="Producto" className="stack-full"><b>{r.name}</b> <span className="muted mono" style={{ fontSize: 12 }}>{r.code}</span>{r.category !== cat.path && <div className="muted" style={{ fontSize: 12 }}>{r.category}</div>}{r.skip && <div className="muted" style={{ fontSize: 12 }}>{r.skip}</div>}{r.price_after != null && r.web_price != null && <div style={{ fontSize: 12, color: "var(--warn)" }}>Precio web propio {fmtMoney(r.web_price, r.currency)}</div>}</td>
                  <td data-label="Precio actual" className="num mono">{fmtMoney(r.price_before, r.currency)}</td>
                  <td data-label="Precio nuevo" className="num mono" style={{ fontWeight: 700 }}>{r.price_after != null ? fmtMoney(r.price_after, r.currency) : "—"}</td>
                  <td data-label="Margen actual" className="num mono">{pct(r.margin_before)}</td>
                  <td data-label="Margen nuevo" className="num mono" style={r.margin_after != null ? { color: tone(Number(r.margin_after)), fontWeight: 700 } : undefined}>{pct(r.margin_after)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </>
      )}
    </Modal>
  );
}
