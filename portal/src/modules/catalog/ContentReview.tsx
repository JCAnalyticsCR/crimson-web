/* Catálogo → Contenido por aprobar (Ola 3, O3-11). Nombres comerciales, especificaciones y descripciones de la tienda
   que propone una regla (sin IA ni servicios externos). Nada de esto se ve en la tienda hasta que el administrador:
   1) revisa el lote (antes y después) y aprueba por fila o el lote entero; 2) revisa a mano una muestra (50 filas, o
   todo el lote si es menor) y marca las que están mal: el error tiene que ser menor al 2 %; 3) aplica. Un lote aplicado
   se puede deshacer y cada producto vuelve exactamente a lo que tenía. Lo escrito a mano nunca se pisa.
   Trato de usted. Tablas anchas con table--stack (tarjetas a 768 px y menos). */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Loading } from "../../ui/components";
import "./productos.css";

type Val = string | Record<string, string> | null;
type Sample = { needed: number; checked: number; wrong: number; error: string | null; ok: boolean };
type Batch = {
  id: number; field: string; field_label: string; rule: string; category: string | null; source: string; status: string;
  counts: Record<string, number>; sample: Sample; created_at: string | null; applied_at: string | null; undone_at: string | null;
};
type Row = { id: number; product_id: number; name: string | null; code: string | null; before: Val; after: Val; flag: string | null; status: string; in_sample: boolean; wrong: boolean };
type Detail = { batch: Batch; items: Row[] };
type FieldOpt = { field: string; label: string; ready: boolean };
type Cat = { id: number; name: string; parent_id: number | null };
type Sim = { field: string; rule: string; batch_id: number | null; counts: Record<string, number>; preview?: { product_id: number; name: string; code: string; before: Val; after: Val; flag: string | null }[] };

const ESTADO: Record<string, string> = { propuesto: "Propuesto", aprobado: "Aprobado", aplicado: "Aplicado", descartado: "Descartado", deshecho: "Deshecho" };
const TONO: Record<string, string> = { propuesto: "badge--info", aprobado: "badge--ok", aplicado: "badge--ok", descartado: "badge--muted", deshecho: "badge--muted" };
const n = (v: number | undefined) => (v ?? 0).toLocaleString("es-CR");

function Valor({ v }: { v: Val }) {
  if (v == null || v === "") return <span className="muted">—</span>;
  if (typeof v === "string") return <span style={{ whiteSpace: "pre-line", overflowWrap: "anywhere" }}>{v}</span>;
  return <ul style={{ margin: 0, paddingLeft: 16 }}>{Object.entries(v).map(([k, x]) => <li key={k}><b>{k}:</b> {x}</li>)}</ul>;
}

export default function ContentReview() {
  const { allows, toast } = useSession();
  const admin = allows("catalog.publicar");
  const edita = allows("catalog.editar");
  const [batches, setBatches] = useState<Batch[] | null>(null);
  const [fields, setFields] = useState<FieldOpt[]>([]);
  const [cats, setCats] = useState<Cat[]>([]);
  const [open, setOpen] = useState<Detail | null>(null);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [sample, setSample] = useState<number[] | null>(null);
  const [wrong, setWrong] = useState<Set<number>>(new Set());
  const [gen, setGen] = useState({ field: "web_name", category_id: "" });
  const [sim, setSim] = useState<Sim | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api<Batch[]>("/store/content/batches").then(setBatches).catch(() => setBatches([])), []);
  useEffect(() => {
    load();
    api<FieldOpt[]>("/store/content/fields").then(setFields).catch(() => setFields([]));
    api<Cat[]>("/categories").then(setCats).catch(() => setCats([]));
  }, [load]);

  const abrir = useCallback((id: number) => {
    setSel(new Set()); setSample(null); setWrong(new Set());
    api<Detail>(`/store/content/batches/${id}`).then(setOpen).catch((e) => toast(String(e.message || e), "bad"));
  }, [toast]);

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try { await fn(); toast(ok); await load(); if (open) abrir(open.batch.id); }
    catch (e) { toast((e as Error).message || "No se pudo completar", "bad"); }
    finally { setBusy(false); }
  };

  const simular = (crear: boolean) => run(async () => {
    const body = { field: gen.field, category_id: gen.category_id ? Number(gen.category_id) : null, simulate: !crear };
    const r = await api<Sim>("/store/content/generate", { method: "POST", json: body });
    setSim(crear ? null : r);
    if (crear && r.batch_id) abrir(r.batch_id);
  }, crear ? "Lote creado en borrador. La tienda no cambió." : "Vista previa lista. Todavía no se guardó nada.");

  const b = open?.batch;
  const abiertas = useMemo(() => (open?.items || []).filter((r) => r.status === "propuesto" || r.status === "aprobado"), [open]);
  const muestra = useMemo(() => new Set(sample || []), [sample]);
  const pedirMuestra = () => b && api<{ draft_ids: number[] }>(`/store/content/batches/${b.id}/sample`).then((r) => { setSample(r.draft_ids); setWrong(new Set()); });
  const filas = (ids: Set<number>) => (ids.size ? { draft_ids: [...ids] } : { draft_ids: null });

  return (
    <>
      <div className="page-head">
        <div><div className="meta">05 · Catálogo</div><h1 className="h1">Contenido por aprobar</h1></div>
        <div className="page-head__actions"><Link className="btn btn--ghost" to="/productos"><Icon d={I.products} />Productos</Link></div>
      </div>
      <p className="muted mrg-help">Aquí revisa los nombres comerciales, especificaciones y descripciones que propone el sistema para la tienda. Nada se publica sin su aprobación: primero aprueba las filas, luego revisa a mano una muestra y, si el error es menor al 2 %, aplica el lote. Si algo no le convence, puede deshacer el lote completo. El nombre fiscal, el código y los precios no cambian.</p>

      {edita && (
        <Card>
          <h2 className="h3" style={{ marginTop: 0 }}>Proponer contenido</h2>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
            <div style={{ minWidth: 0, flex: "1 1 200px" }}><Field label="Campo">
              <select aria-label="Campo" value={gen.field} onChange={(e) => { setGen({ ...gen, field: e.target.value }); setSim(null); }}>
                {fields.map((f) => <option key={f.field} value={f.field} disabled={!f.ready}>{f.label}{f.ready ? "" : " (sin regla todavía)"}</option>)}
              </select>
            </Field></div>
            <div style={{ minWidth: 0, flex: "1 1 200px" }}><Field label="Categoría">
              <select aria-label="Categoría" value={gen.category_id} onChange={(e) => { setGen({ ...gen, category_id: e.target.value }); setSim(null); }}>
                <option value="">Todas las publicadas</option>
                {cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </Field></div>
            <button className="btn btn--soft" disabled={busy} onClick={() => simular(false)}>Ver vista previa</button>
            {sim && !!sim.counts.propuestos && <button className="btn btn--crimson" disabled={busy} onClick={() => simular(true)}>Crear lote en borrador</button>}
          </div>
          {sim && (
            <div style={{ marginTop: 12 }}>
              <p className="muted">{n(sim.counts.productos)} productos revisados: {n(sim.counts.propuestos)} con propuesta ({n(sim.counts.a_revision)} marcadas para revisar), {n(sim.counts.sin_cambio)} ya están así, {n(sim.counts.sin_dato)} sin datos suficientes, {n(sim.counts.ya_propuesto)} ya propuestos en otro lote.</p>
              {!!sim.preview?.length && (
                <table className="table table--sm table--stack">
                  <thead><tr><th>Producto</th><th>Antes</th><th>Propuesta</th><th>Revisar</th></tr></thead>
                  <tbody>{sim.preview.slice(0, 50).map((r) => (
                    <tr key={r.product_id}>
                      <td data-label="Producto" className="stack-full"><b>{r.name}</b><div className="muted mono" style={{ fontSize: 12 }}>{r.code}</div></td>
                      <td data-label="Antes"><Valor v={r.before} /></td>
                      <td data-label="Propuesta"><Valor v={r.after} /></td>
                      <td data-label="Revisar">{r.flag ? <span className="badge badge--warn">{r.flag}</span> : <span className="muted">—</span>}</td>
                    </tr>
                  ))}</tbody>
                </table>
              )}
            </div>
          )}
        </Card>
      )}

      <Card flush>
        {batches === null ? <Loading /> : batches.length === 0 ? <Empty hint="Todavía no hay lotes. Proponga contenido para una categoría y revise la vista previa." /> : (
          <table className="table table--stack">
            <thead><tr><th>Lote</th><th>Campo</th><th>Categoría</th><th>Estado</th><th className="num">Filas</th><th>Muestra</th><th /></tr></thead>
            <tbody>{batches.map((x) => (
              <tr key={x.id} className={open?.batch.id === x.id ? "is-sel" : undefined}>
                <td data-label="Lote" className="stack-full"><b>#{x.id}</b> <span className="muted">{x.source === "alta" ? "productos nuevos" : x.rule}</span></td>
                <td data-label="Campo">{x.field_label}</td>
                <td data-label="Categoría">{x.category || <span className="muted">Todas</span>}</td>
                <td data-label="Estado"><span className={`badge ${TONO[x.status] || "badge--muted"}`}>{ESTADO[x.status] || x.status}</span></td>
                <td data-label="Filas" className="num mono">{n(Object.values(x.counts).reduce((a, c) => a + c, 0))}</td>
                <td data-label="Muestra">{x.sample.ok ? <span className="badge badge--ok">Lista ({x.sample.checked})</span> : <span className="muted">{x.sample.checked}/{x.sample.needed}</span>}</td>
                <td className="stack-actions"><button className="btn btn--ghost btn--sm" onClick={() => (open?.batch.id === x.id ? setOpen(null) : abrir(x.id))} aria-expanded={open?.batch.id === x.id}>{open?.batch.id === x.id ? "Cerrar" : "Revisar"}</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>

      {open && b && (
        <Card>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <h2 className="h3" style={{ margin: 0, marginRight: "auto" }}>Lote #{b.id} · {b.field_label}</h2>
            {b.status === "propuesto" && admin && <button className="btn btn--soft btn--sm" disabled={busy || !abiertas.length} onClick={() => run(() => api(`/store/content/batches/${b.id}/approve`, { method: "POST", json: filas(sel) }), sel.size ? "Filas aprobadas." : "Lote aprobado.")}>{sel.size ? `Aprobar ${sel.size} seleccionadas` : "Aprobar todo el lote"}</button>}
            {b.status === "propuesto" && edita && <button className="btn btn--ghost btn--sm" disabled={busy || !abiertas.length} onClick={() => run(() => api(`/store/content/batches/${b.id}/discard`, { method: "POST", json: filas(sel) }), "Filas descartadas.")}>{sel.size ? `Descartar ${sel.size}` : "Descartar el lote"}</button>}
            {b.status === "propuesto" && admin && <button className="btn btn--ghost btn--sm" disabled={busy} onClick={pedirMuestra}>Revisar muestra</button>}
            {b.status === "propuesto" && admin && <button className="btn btn--crimson btn--sm" disabled={busy || !b.sample.ok} title={b.sample.ok ? "" : "Primero registre la muestra revisada"} onClick={() => run(() => api(`/store/content/batches/${b.id}/apply`, { method: "POST" }), "Lote aplicado: la tienda ya muestra lo aprobado.")}>Aplicar a la tienda</button>}
            {b.status === "aplicado" && admin && <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => run(() => api(`/store/content/batches/${b.id}/undo`, { method: "POST" }), "Lote deshecho: cada producto volvió a lo que tenía.")}>Deshacer el lote</button>}
          </div>
          <p className="muted" style={{ fontSize: 13 }}>Muestra: {b.sample.checked} de {b.sample.needed} revisadas, {b.sample.wrong} con error{b.sample.ok ? " · lista para aplicar" : " · se necesita menos de 2 % de error"}.</p>
          {sample && (
            <div className="mrg-help" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <span>Revise las {sample.length} filas marcadas «Muestra» y desmarque «Correcta» en las que estén mal.</span>
              <button className="btn btn--crimson btn--sm" disabled={busy} onClick={() => run(() => api(`/store/content/batches/${b.id}/sample`, { method: "POST", json: { checked: sample, wrong: [...wrong] } }), "Muestra registrada.")}>Registrar muestra</button>
            </div>
          )}
          <table className="table table--sm table--stack">
            <thead><tr><th aria-label="Elegir" /><th>Producto</th><th>Antes</th><th>Después</th><th>Estado</th></tr></thead>
            <tbody>{open.items.filter((r) => !sample || muestra.has(r.id) || r.status !== "descartado").map((r) => (
              <tr key={r.id}>
                <td data-label="Elegir">
                  {b.status === "propuesto" && (r.status === "propuesto" || r.status === "aprobado") && <input type="checkbox" aria-label={`Elegir ${r.name}`} checked={sel.has(r.id)} onChange={() => { const s = new Set(sel); if (s.has(r.id)) s.delete(r.id); else s.add(r.id); setSel(s); }} />}
                </td>
                <td data-label="Producto" className="stack-full"><b>{r.name}</b><div className="muted mono" style={{ fontSize: 12 }}>{r.code}</div>{r.flag && <span className="badge badge--warn">{r.flag}</span>}</td>
                <td data-label="Antes"><Valor v={r.before} /></td>
                <td data-label="Después"><Valor v={r.after} /></td>
                <td data-label="Estado">
                  <span className={`badge ${TONO[r.status] || "badge--muted"}`}>{ESTADO[r.status] || r.status}</span>
                  {muestra.has(r.id) && <label style={{ display: "flex", gap: 4, alignItems: "center", marginTop: 4 }}><input type="checkbox" checked={!wrong.has(r.id)} onChange={() => { const s = new Set(wrong); if (s.has(r.id)) s.delete(r.id); else s.add(r.id); setWrong(s); }} />Muestra: correcta</label>}
                  {!sample && r.in_sample && <div className="muted" style={{ fontSize: 12 }}>{r.wrong ? "Con error en la muestra" : "Revisada en la muestra"}</div>}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </Card>
      )}
    </>
  );
}
