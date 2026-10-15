/* Inventarios -> Proveedores. El catálogo de cada proveedor (Eurocomp, …) aparte de lo que Crimson tiene en bodega:
   Andrés pidió verlo "de una manera categorizada diferente para no mezclarlos con lo que nosotros tenemos localmente".
   Entra hoy por la lista en Excel; el conector por API (SiReTT) usará el mismo catálogo. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney, uploadFile } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Loading, Modal } from "../../ui/components";
import { Lookup, searchProducts, type LookupItem } from "../../ui/Lookup";
import "./proveedores.css";

type Num = string | number | null | undefined;
type Cat = { category: string | null; subcategory: string | null; count: number };
type Sup = {
  id: number; name: string; items: number; active: number; linked: number; in_stock: number; categories: Cat[];
  last_list: { list_date: string; source: string; source_file: string | null; imported_at: string; summary: Record<string, number> | null } | null;
  api: { status: string; label: string | null; last_ok_at?: string; last_try_at?: string; last_summary?: Record<string, number> };
};
type LinkedProduct = { id: number; code: string; name: string; active: boolean; price?: Num; currency?: string };
type Item = {
  id: number; supplier_id: number; supplier: string | null; code: string; name: string; description: string | null; brand: string | null; model: string | null;
  category: string | null; subcategory: string | null; stock: number | null; currency: string; list_date: string | null; active: boolean; source: string;
  source_file: string | null; link_method: string | null; linked_at: string | null; product: LinkedProduct | null; cost?: Num; prev_cost?: Num; cost_crc?: Num;
};
type Page = { items: Item[]; total: number; offset: number; next_offset: number | null; fx: Num };
type Suggestion = { product_id: number; code: string; name: string; model: string | null; reason: string; label: string; score: number; already_linked?: boolean };
type Proposal = { code: string; margin_pct: Num; currency: string; price: Num; category_id: number | null; fx: Num };
type Detail = Item & { suggestions: Suggestion[]; raw: Record<string, unknown> | null; proposal?: Proposal };
type PRow = {
  row: number; action: string; base?: string; code: string | null; name?: string; model?: string | null; category?: string | null; subcategory?: string | null;
  stock?: number | null; cost?: Num; prev_cost?: Num; currency?: string; changed?: string[]; warnings: string[]; detail: string | null;
};
type Preview = {
  format: string; header_row: number; headers: string[]; columns: Record<string, string | null>; labels: Record<string, string>;
  supplier: { id: number | null; name: string; new: boolean }; list_date: string; rows: number; summary: Record<string, number>; categories: Cat[];
  result: PRow[]; inactivar: { code: string; name: string; product_id: number | null }[];
  vinculos: { code: string; name: string; product_id: number; product_code: string; product_name: string; method: string }[];
  productos: { product_id: number; code: string; name: string; cost_before: Num; cost_after: Num; currency: string; stock_before: number | null; stock_after: number | null; margin_before: Num; margin_after: Num }[];
  commit: boolean; fx: Num;
};
type Named = { id: number; name: string };
type CatFilter = { category: string | null; subcategory: string | null } | null;

const n = (v: Num) => Number(v ?? 0);
const crc = (v: Num) => (v == null || v === "" ? "—" : fmtMoney(Math.round(n(v)), "CRC"));
const money = (v: Num, cur = "USD") => (v == null || v === "" ? "—" : fmtMoney(v, cur));
const today = () => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
const METHOD: Record<string, string> = { sku: "por código", modelo: "por modelo", nombre: "por nombre", manual: "confirmado", creado: "creado desde aquí" };
const ACTIONS: [string, string, string][] = [
  ["nuevo", "Nuevos", "ok"], ["actualizado", "Actualizados", "info"], ["sin_cambios", "Sin cambios", "muted"], ["dudoso", "Dudosos", "warn"], ["error", "Errores", "bad"],
];

export default function SupplierCatalog() {
  const { toast, allows } = useSession();
  /* toast cambia de identidad en cada render del proveedor de sesion: si fuera dependencia de los efectos,
     un error al cargar (que muestra un toast) volveria a disparar la carga sin fin. */
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const verCostos = allows("catalog.costos");
  const canLink = allows("catalog.editar");
  const canAdd = allows("catalog.crear") && verCostos;
  const [sups, setSups] = useState<Sup[] | null>(null);
  const [sid, setSid] = useState<number | null>(null);
  const [cat, setCat] = useState<CatFilter>(null);
  const [q, setQ] = useState("");
  const [f, setF] = useState({ linked: "", stock: "", estado: "activos" });
  const [page, setPage] = useState<Page | null>(null);
  const [more, setMore] = useState(false);
  const [importing, setImporting] = useState(false);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [mode, setMode] = useState<"link" | "add" | null>(null);
  const [sugOpen, setSugOpen] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const loadSups = useCallback(() => api<Sup[]>("/supplier-catalog/suppliers").then((r) => {
    setSups(r);
    setSid((cur) => (cur && r.some((s) => s.id === cur) ? cur : (r.find((s) => s.items > 0)?.id ?? null)));
  }).catch((e) => { toastRef.current(e instanceof Error ? e.message : "Error", "bad"); setSups([]); }), []);
  useEffect(() => { loadSups(); }, [loadSups]);
  /* Proveedor conectado por API (SiReTT de Eurocomp): se sincroniza solo cada hora; el boton adelanta la proxima. */
  const apiSup = (sups || []).find((s) => ["conectado", "listo", "error"].includes(s.api.status));
  const syncNow = () => {
    if (!apiSup) return;
    setSyncing(true);
    api<Record<string, number>>(`/supplier-catalog/suppliers/${apiSup.id}/sync`, { method: "POST" })
      .then((r) => toast(`Sincronizado: ${r.utiles} artículos útiles de ${r.recibidos} · ${r.nuevo} nuevos · ${r.actualizado} actualizados · ${r.vinculos} vínculos nuevos`))
      .catch((e) => toast(e instanceof Error ? e.message : "Error", "bad"))
      .finally(() => { setSyncing(false); loadSups(); });
  };

  const query = useCallback((offset = 0) => {
    const p = new URLSearchParams({ limit: "60", offset: String(offset), estado: f.estado });
    if (sid) p.set("supplier_id", String(sid));
    if (cat) { p.set("category", cat.category ?? ""); p.set("subcategory", cat.subcategory ?? ""); } // vacio = sin (sub)categoria
    if (q.trim()) p.set("q", q.trim());
    if (f.linked) p.set("linked", f.linked);
    if (f.stock) p.set("stock", f.stock);
    return api<Page>(`/supplier-catalog/items?${p}`);
  }, [sid, cat, q, f]);
  const load = useCallback(() => { if (!sid) { setPage(null); return; } setPage(null); query(0).then(setPage).catch((e) => toastRef.current(e instanceof Error ? e.message : "Error", "bad")); }, [sid, query]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);
  const loadMore = async () => {
    if (!page?.next_offset) return;
    setMore(true);
    try { const r = await query(page.next_offset); setPage({ ...r, items: [...page.items, ...r.items] }); } finally { setMore(false); }
  };

  const sup = sups?.find((s) => s.id === sid) || null;
  const groups = useMemo(() => {
    const g = new Map<string, Cat[]>();
    for (const c of sup?.categories || []) { const k = c.category || "Sin categoría"; g.set(k, [...(g.get(k) || []), c]); }
    return [...g.entries()];
  }, [sup]);
  const refresh = () => { loadSups(); load(); };

  const openItem = async (it: Item, m: "link" | "add") => {
    try { const d = await api<Detail>(`/supplier-catalog/items/${it.id}`); setDetail(d); setMode(m); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  if (sups === null) return <Loading />;
  const withItems = sups.filter((s) => s.items > 0);

  return (
    <>
      <div className="prv-banner" role="note">
        <span className="prv-banner__tag">Catálogo del proveedor</span>
        <span>Esto <b>no es inventario propio</b>: es lo que cada proveedor tiene para vendernos (bajo pedido), con sus códigos y sus categorías. Tus existencias en bodega están en <b>Existencias</b>. Un ítem del proveedor solo pasa a tu catálogo cuando lo vinculás o lo agregás.</span>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <h3 className="h3" style={{ margin: 0 }}>Proveedores</h3>
        <span className="muted" style={{ fontSize: 13 }}>{withItems.length ? `${withItems.length} con catálogo cargado` : "Ninguno con catálogo todavía"}</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          {canLink && withItems.length > 0 && <button className="btn btn--soft btn--sm" onClick={() => setSugOpen(true)}><Icon d={I.link} />Sugerencias de vínculo</button>}
          {canAdd && apiSup && <button className="btn btn--soft btn--sm" onClick={syncNow} disabled={syncing} title="Eurocomp permite una consulta del catálogo completo por hora"><Icon d={I.refresh} />{syncing ? "Sincronizando…" : `Sincronizar ${apiSup.name.split(" ")[0]}`}</button>}
          {canAdd && <button className="btn btn--crimson btn--sm" onClick={() => setImporting(true)}><Icon d={I.upload} />Importar lista</button>}
        </div>
      </div>

      {withItems.length === 0 ? (
        <Card><Empty title="Todavía no hay catálogos de proveedor" hint="Subí la lista de precios de Eurocomp tal como la manda el proveedor (Excel). Se ve primero una vista previa y nada se mezcla con tu inventario." action={canAdd ? <button className="btn btn--crimson" onClick={() => setImporting(true)}><Icon d={I.upload} />Importar lista</button> : undefined} /></Card>
      ) : (
        <div className="prv-sups">
          {withItems.map((s) => (
            <button key={s.id} className={`prv-sup${s.id === sid ? " is-active" : ""}`} onClick={() => { setSid(s.id); setCat(null); }} aria-pressed={s.id === sid}>
              <span className="prv-sup__name">{s.name}</span>
              <span className="prv-sup__nums"><span><b>{s.active}</b> ítems</span><span><b>{s.in_stock}</b> con existencia</span><span><b>{s.linked}</b> vinculados</span></span>
              <span className="meta">{s.last_list ? `Lista del ${fmtDate(s.last_list.list_date)}${s.last_list.source_file ? ` · ${s.last_list.source_file}` : ""}` : "Sin lista"}</span>
              {s.api.label && <span className="prv-api" data-status={s.api.status}><i />{s.api.label}{s.api.last_ok_at ? ` · ${new Date(s.api.last_ok_at).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}` : ""}</span>}
            </button>
          ))}
        </div>
      )}

      {sup && sup.items > 0 && (
        <div className="prv-layout">
          <nav className="prv-cats" aria-label="Categorías del proveedor">
            <button className={cat === null ? "is-active" : ""} onClick={() => setCat(null)}><span>Todas</span><span>{sup.active}</span></button>
            {groups.map(([g, subs]) => (
              <div key={g}>
                <div className="prv-cats__group">{g}</div>
                {subs.map((c) => {
                  const on = cat !== null && cat.category === c.category && cat.subcategory === c.subcategory;
                  return <button key={`${c.category}|${c.subcategory}`} className={on ? "is-active" : ""} onClick={() => setCat({ category: c.category, subcategory: c.subcategory })}><span>{c.subcategory || c.category || "Sin categoría"}</span><span>{c.count}</span></button>;
                })}
              </div>
            ))}
          </nav>

          <Card flush title={<>{sup.name}{cat ? <span className="muted" style={{ fontWeight: 500 }}> · {[cat.category, cat.subcategory].filter(Boolean).join(" · ")}</span> : null}</>} extra={page ? <span className="meta">{page.total} {page.total === 1 ? "ítem" : "ítems"}{verCostos && page.fx ? ` · TC ${n(page.fx).toFixed(2)}` : ""}</span> : undefined}>
            <div className="list-head" style={{ flexWrap: "wrap" }}>
              <div className="search" style={{ maxWidth: 320 }}><Icon d={I.search} size={16} /><input placeholder="Código, descripción o modelo…" aria-label="Buscar en el catálogo del proveedor" value={q} onChange={(e) => setQ(e.target.value)} /></div>
              <select className="select select--sm" value={f.linked} onChange={(e) => setF({ ...f, linked: e.target.value })} aria-label="Vínculo"><option value="">Vinculados y sin vincular</option><option value="si">Vinculados a mi catálogo</option><option value="no">Sin vincular</option></select>
              <select className="select select--sm" value={f.stock} onChange={(e) => setF({ ...f, stock: e.target.value })} aria-label="Existencias"><option value="">Toda existencia</option><option value="con">Con existencia</option><option value="sin">Sin existencia</option></select>
              <select className="select select--sm" value={f.estado} onChange={(e) => setF({ ...f, estado: e.target.value })} aria-label="Estado"><option value="activos">En la lista vigente</option><option value="inactivos">Ya no vienen en la lista</option><option value="todos">Todos</option></select>
              <button className="btn btn--ghost btn--sm" onClick={refresh} style={{ marginLeft: "auto" }} aria-label="Actualizar"><Icon d={I.refresh} /></button>
            </div>
            {page === null ? <Loading /> : page.items.length === 0 ? <Empty hint="Nada con esos filtros en el catálogo de este proveedor." /> : (
              <div className="prv-scroll">
                <table className="table">
                  <thead><tr>
                    <th>Código</th><th>Descripción</th><th>Modelo</th><th className="num" title="Existencia que reporta el proveedor, no tu bodega">Exist. prov.</th>
                    {verCostos && <th className="num" title="Precio del proveedor sin IVA = tu costo. En colones con el tipo de cambio del día.">Costo</th>}
                    <th>Vinculado a</th><th />
                  </tr></thead>
                  <tbody>{page.items.map((it) => (
                    <tr key={it.id} className={it.active ? undefined : "prv-off"}>
                      <td className="mono" style={{ whiteSpace: "nowrap" }}>{it.code}</td>
                      <td style={{ minWidth: 230 }}>
                        <div className="prv-name prv-clamp" title={it.name}>{it.name}</div>
                        <div className="meta">{[it.brand, it.subcategory || it.category].filter(Boolean).join(" · ")}{!it.active && " · ya no viene en la lista"}</div>
                      </td>
                      <td className="mono" style={{ fontSize: 12, overflowWrap: "anywhere", minWidth: 110, maxWidth: 150 }}>{it.model || <span className="muted">—</span>}</td>
                      <td className="num mono" style={{ fontWeight: 700, color: n(it.stock) > 0 ? undefined : "var(--text-3)" }}>{it.stock ?? "—"}</td>
                      {verCostos && (
                        <td className="num mono" style={{ whiteSpace: "nowrap" }}>
                          {money(it.cost, it.currency)}
                          {it.currency !== "CRC" && <div className="meta">{crc(it.cost_crc)}</div>}
                          {it.prev_cost != null && it.cost != null && n(it.prev_cost) !== n(it.cost) && <div className="meta" style={{ color: n(it.cost) > n(it.prev_cost) ? "var(--bad)" : "var(--ok)" }}>antes {money(it.prev_cost, it.currency)}</div>}
                        </td>
                      )}
                      <td style={{ maxWidth: 190 }}>{it.product ? (
                        <span className="prv-link" title={`${it.product.code} · ${it.product.name}`}><b className="mono">{it.product.code}</b><span className="prv-clamp" style={{ fontSize: 12.5 }}>{it.product.name}</span><span className="meta">{METHOD[it.link_method || ""] || "vinculado"}</span></span>
                      ) : <span className="prv-chip">Sin vincular</span>}</td>
                      <td><div className="prv-actions">
                        {canLink && <button className="btn btn--ghost btn--sm" onClick={() => openItem(it, "link")}>{it.product ? "Vínculo" : "Vincular"}</button>}
                        {canAdd && !it.product && it.active && <button className="btn btn--soft btn--sm" onClick={() => openItem(it, "add")} title="Agregar a mi catálogo: crea el producto con el costo de este proveedor" aria-label="Agregar a mi catálogo"><Icon d={I.plus} size={14} />Agregar</button>}
                        {!canLink && !canAdd && <button className="btn btn--ghost btn--sm" onClick={() => openItem(it, "link")}>Ver</button>}
                      </div></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
            {page?.next_offset != null && <div style={{ padding: 12, textAlign: "center" }}><button className="btn btn--ghost btn--sm" onClick={loadMore} disabled={more}>{more ? <span className="spinner" /> : `Más resultados (${page.total - page.items.length})`}</button></div>}
          </Card>
        </div>
      )}

      {importing && <ImportModal sups={sups} current={sid} onClose={() => setImporting(false)} onDone={(id) => { setImporting(false); setSid(id); setCat(null); loadSups(); load(); }} />}
      {detail && mode === "link" && <LinkModal item={detail} canLink={canLink} onClose={() => setDetail(null)} onDone={() => { setDetail(null); refresh(); }} />}
      {detail && mode === "add" && <AddModal item={detail} onClose={() => setDetail(null)} onDone={() => { setDetail(null); refresh(); }} />}
      {sugOpen && <SuggestionsModal supplierId={sid} onClose={() => setSugOpen(false)} onDone={() => { setSugOpen(false); refresh(); }} />}
    </>
  );
}

/* ---------- importar lista con vista previa ---------- */
function ImportModal({ sups, current, onClose, onDone }: { sups: Sup[]; current: number | null; onClose: () => void; onDone: (supplierId: number) => void }) {
  const { toast } = useSession();
  const fileRef = useRef<HTMLInputElement>(null);
  const [form, setForm] = useState({ supplier_id: current ? String(current) : (sups[0] ? String(sups[0].id) : "__new"), supplier: "", list_date: today(), currency: "USD", brand: "" });
  const [file, setFile] = useState<File | null>(null);
  const [prev, setPrev] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [show, setShow] = useState("");

  const qs = (commit: boolean, maps: Record<string, string>) => {
    const p = new URLSearchParams({ commit: String(commit), list_date: form.list_date, currency: form.currency });
    if (form.supplier_id === "__new") p.set("supplier", form.supplier.trim()); else p.set("supplier_id", form.supplier_id);
    if (form.brand.trim()) p.set("brand", form.brand.trim());
    const m = Object.fromEntries(Object.entries(maps).filter(([, v]) => v));
    if (Object.keys(m).length) p.set("mapping", JSON.stringify(m));
    return p.toString();
  };
  const preview = async (fl: File, maps = mapping) => {
    if (form.supplier_id === "__new" && !form.supplier.trim()) { toast("Escribí el nombre del proveedor", "bad"); return; }
    setBusy(true);
    try { const r = await uploadFile<Preview>(`/supplier-catalog/import?${qs(false, maps)}`, fl); setPrev(r); setShow(""); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo leer la lista", "bad"); }
    finally { setBusy(false); }
  };
  const confirm = async () => {
    if (!file || !prev) return;
    setBusy(true);
    try {
      const r = await uploadFile<Preview>(`/supplier-catalog/import?${qs(true, mapping)}`, file);
      const s = r.summary;
      toast(`Lista importada: ${s.nuevo} nuevos, ${s.actualizado} actualizados, ${s.inactivar} inactivos, ${s.vinculos} vinculados`);
      onDone(r.supplier.id as number);
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo importar", "bad"); }
    finally { setBusy(false); }
  };
  const pick = (fl: File) => { setFile(fl); setMapping({}); preview(fl, {}); };
  const s = prev?.summary;
  const rows = (prev?.result || []).filter((r) => !show || r.action === show);
  const toImport = s ? s.nuevo + s.actualizado + s.dudoso + s.sin_cambios : 0;

  return (
    <Modal wide title={prev ? `Vista previa · ${prev.supplier.name}` : "Importar lista de proveedor"} onClose={onClose} foot={<>
      {prev && <button className="btn btn--ghost" style={{ marginRight: "auto" }} onClick={() => { setPrev(null); setFile(null); }}>Cambiar archivo</button>}
      <button className="btn btn--ghost" onClick={onClose}>Cancelar</button>
      {prev && <button className="btn btn--crimson" onClick={confirm} disabled={busy || toImport === 0}>{busy ? <span className="spinner" /> : `Importar lista (${toImport} ítems)`}</button>}
    </>}>
      {!prev && <>
        <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Subí el archivo tal como lo manda el proveedor. El formato de Eurocomp (títulos arriba, filas de categoría) se reconoce solo; para otros proveedores podés indicar qué columna es cada dato. Primero ves qué va a cambiar; nada se guarda hasta confirmar.</p>
        <div className="grid-2">
          <Field label="Proveedor">
            <select className="select" value={form.supplier_id} onChange={(e) => setForm({ ...form, supplier_id: e.target.value })}>
              {sups.map((x) => <option key={x.id} value={x.id}>{x.name}{x.items ? ` · ${x.items} ítems` : ""}</option>)}
              <option value="__new">+ Proveedor nuevo…</option>
            </select>
          </Field>
          {form.supplier_id === "__new" && <Field label="Nombre del proveedor"><input className="input" value={form.supplier} placeholder="Eurocomp Costa Rica" onChange={(e) => setForm({ ...form, supplier: e.target.value })} /></Field>}
          <Field label="Fecha de la lista" hint="La tienda usa esta fecha para saber si la existencia del proveedor está vigente."><input className="input" type="date" max={today()} value={form.list_date} onChange={(e) => setForm({ ...form, list_date: e.target.value })} /></Field>
          <Field label="Divisa de los precios"><select className="select" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}><option>USD</option><option>CRC</option></select></Field>
          <Field label="Marca por defecto" hint="Si la descripción no la trae."><input className="input" value={form.brand} placeholder="Hikvision" onChange={(e) => setForm({ ...form, brand: e.target.value })} /></Field>
        </div>
        <div className="prv-drop" role="button" tabIndex={0} onClick={() => fileRef.current?.click()} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") fileRef.current?.click(); }}
          onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const fl = e.dataTransfer.files?.[0]; if (fl) pick(fl); }}>
          <input ref={fileRef} type="file" accept=".xlsx,.xlsm,.csv" hidden onChange={(e) => { const fl = e.target.files?.[0]; if (fl) pick(fl); e.target.value = ""; }} />
          {busy ? <span className="spinner" /> : <><Icon d={I.upload} /> Arrastrá la lista (.xlsx o .csv) o hacé clic para elegirla</>}
        </div>
      </>}

      {prev && s && <>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
          <span className={`prv-chip ${prev.format === "eurocomp" ? "prv-chip--ok" : "prv-chip--info"}`}>{prev.format === "eurocomp" ? "Formato Eurocomp reconocido" : "Formato genérico"}</span>
          <span className="meta">{file?.name} · encabezado en la fila {prev.header_row} · {prev.rows} filas · {prev.categories.length} categorías · lista del {fmtDate(prev.list_date)}{prev.supplier.new ? " · proveedor nuevo" : ""}</span>
        </div>
        <div className="prv-sum">
          {ACTIONS.map(([k, l, tone]) => <button key={k} className={show === k ? "is-active" : ""} onClick={() => setShow(show === k ? "" : k)}><span>{l}</span><b style={{ color: tone === "muted" ? undefined : `var(--${tone})` }}>{s[k] ?? 0}</b></button>)}
          <button onClick={() => setShow("")} title="Ítems que ya no vienen: quedan inactivos, no se borran"><span>Se inactivan</span><b style={{ color: s.inactivar ? "var(--warn)" : undefined }}>{s.inactivar}</b></button>
          <button onClick={() => setShow("")} title="Código del proveedor o modelo exacto"><span>Vínculos automáticos</span><b>{s.vinculos}</b></button>
          <button onClick={() => setShow("")} title="Para confirmar a mano (modelo parecido o nombre)"><span>Sugerencias</span><b>{s.sugerencias}</b></button>
          <button onClick={() => setShow("")} title="Tus productos vinculados que cambian de costo o existencia"><span>Productos propios</span><b>{s.productos}</b></button>
        </div>

        {s.inactivar > 0 && s.inactivar >= s.nuevo + s.actualizado + s.sin_cambios + s.dudoso && (
          <div className="prv-banner" role="alert" style={{ marginTop: 12, borderColor: "var(--warn)", background: "var(--warn-soft)" }}>
            <span className="prv-banner__tag" style={{ color: "var(--warn)", borderColor: "var(--warn)" }}>Ojo</span>
            <span><b>{s.inactivar} ítems</b> que hoy están en el catálogo de este proveedor no vienen en el archivo y quedarán inactivos{s.productos ? <>; <b>{s.productos} productos tuyos</b> se quedan sin existencia del proveedor</> : null}. Si es una lista parcial, no la importes así: subí la lista completa.</span>
          </div>
        )}

        <div className="prv-section">
          <h4>Columnas</h4>
          <div className="prv-map">
            {Object.entries(prev.labels).map(([field, label]) => (
              <Field key={field} label={label}>
                <select className="select select--sm" value={mapping[field] ?? prev.columns[field] ?? ""} onChange={(e) => setMapping({ ...mapping, [field]: e.target.value })}>
                  <option value="">— no viene —</option>
                  {prev.headers.map((h) => <option key={h} value={h}>{h}</option>)}
                </select>
              </Field>
            ))}
          </div>
          {Object.keys(mapping).length > 0 && <button className="btn btn--soft btn--sm" style={{ marginTop: 8 }} onClick={() => file && preview(file)} disabled={busy}>Aplicar columnas y volver a previsualizar</button>}
        </div>

        <div className="prv-section">
          <h4>{show ? ACTIONS.find(([k]) => k === show)?.[1] : "Filas"} · {rows.length}</h4>
          <div className="prv-list">
            <table className="table">
              <thead><tr><th className="num">Fila</th><th>Código</th><th>Descripción</th><th>Categoría</th><th className="num">Exist.</th><th className="num">Costo</th><th>Nota</th></tr></thead>
              <tbody>{rows.slice(0, 400).map((r) => (
                <tr key={`${r.row}-${r.code}`}>
                  <td className="num mono muted">{r.row}</td>
                  <td className="mono">{r.code || "—"}</td>
                  <td>{r.name || r.detail}{r.model && <div className="meta mono">{r.model}</div>}</td>
                  <td className="muted">{[r.category, r.subcategory].filter(Boolean).join(" · ") || "—"}</td>
                  <td className="num mono">{r.stock ?? "—"}</td>
                  <td className="num mono" style={{ whiteSpace: "nowrap" }}>{r.cost != null ? money(r.cost, r.currency) : "—"}{r.prev_cost != null && <div className="meta">antes {money(r.prev_cost, r.currency)}</div>}</td>
                  <td style={{ fontSize: 12 }}>
                    {r.action === "error" ? <span className="prv-chip prv-chip--bad">{r.detail}</span> : <>
                      <span className={`prv-chip ${r.action === "dudoso" ? "prv-chip--warn" : r.action === "nuevo" ? "prv-chip--ok" : r.action === "actualizado" ? "prv-chip--info" : ""}`}>{ACTIONS.find(([k]) => k === r.action)?.[1].replace(/s$/, "") || r.action}</span>
                      {r.warnings.length > 0 && <div style={{ color: "var(--warn)", marginTop: 2 }}>{r.warnings.join(" · ")}</div>}
                      {r.action === "actualizado" && r.changed?.length ? <div className="meta">{r.changed.join(", ")}</div> : null}
                    </>}
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          {rows.length > 400 && <p className="meta">Se muestran 400 de {rows.length}.</p>}
        </div>

        {prev.inactivar.length > 0 && <div className="prv-section"><h4>Ya no vienen en la lista · quedan inactivos ({prev.inactivar.length})</h4>
          <div className="prv-list"><table className="table"><tbody>{prev.inactivar.map((x) => <tr key={x.code}><td className="mono">{x.code}</td><td>{x.name}</td><td className="muted">{x.product_id ? "vinculado: tu producto queda sin existencia del proveedor" : ""}</td></tr>)}</tbody></table></div></div>}
        {prev.vinculos.length > 0 && <div className="prv-section"><h4>Vínculos automáticos con tu catálogo ({prev.vinculos.length})</h4>
          <div className="prv-list"><table className="table"><tbody>{prev.vinculos.map((x) => <tr key={x.code}><td className="mono">{x.code}</td><td>{x.name}</td><td>→ <b>{x.product_name}</b> <span className="meta mono">{x.product_code}</span></td><td className="muted">{METHOD[x.method]}</td></tr>)}</tbody></table></div></div>}
        {prev.productos.length > 0 && <div className="prv-section"><h4>Productos de tu catálogo que cambian de costo o existencia ({prev.productos.length})</h4>
          <div className="prv-list"><table className="table">
            <thead><tr><th>Producto</th><th className="num">Costo</th><th className="num">Exist. proveedor</th><th className="num">Margen sobre venta</th></tr></thead>
            <tbody>{prev.productos.map((x) => <tr key={x.product_id}><td>{x.name}<div className="meta mono">{x.code}</div></td>
              <td className="num mono" style={{ whiteSpace: "nowrap" }}>{money(x.cost_before, x.currency)} → <b>{money(x.cost_after, x.currency)}</b></td>
              <td className="num mono">{x.stock_before ?? "—"} → <b>{x.stock_after ?? "—"}</b></td>
              <td className="num mono" style={{ color: x.margin_after != null && x.margin_before != null && n(x.margin_after) < n(x.margin_before) ? "var(--bad)" : undefined }}>{x.margin_before != null ? `${n(x.margin_before)} %` : "—"} → <b>{x.margin_after != null ? `${n(x.margin_after)} %` : "—"}</b></td></tr>)}</tbody>
          </table></div>
          <p className="muted" style={{ fontSize: 12.5 }}>El precio de venta no cambia solo: revisalo en Productos & Servicios si el margen quedó bajo.</p></div>}
      </>}
    </Modal>
  );
}

/* ---------- vincular con un producto propio ---------- */
function LinkModal({ item, canLink, onClose, onDone }: { item: Detail; canLink: boolean; onClose: () => void; onDone: () => void }) {
  const { toast } = useSession();
  const [busy, setBusy] = useState(false);
  const [pick, setPick] = useState<LookupItem | null>(null);
  const link = async (pid: number) => {
    setBusy(true);
    try { await api(`/supplier-catalog/items/${item.id}/link`, { method: "POST", json: { product_id: pid } }); toast("Vinculado: el producto toma el costo y la existencia del proveedor"); onDone(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  const unlink = async () => {
    setBusy(true);
    try { await api(`/supplier-catalog/items/${item.id}/link`, { method: "DELETE" }); toast("Vínculo quitado"); onDone(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <Modal wide title={`${item.code} · ${item.supplier || "Proveedor"}`} onClose={onClose} foot={<button className="btn btn--ghost" onClick={onClose}>Cerrar</button>}>
      <ItemHead item={item} />
      {item.product ? (
        <div className="prv-sug__row" style={{ marginTop: 12 }}>
          <div><div className="meta">Vinculado a tu catálogo · {METHOD[item.link_method || ""] || ""}</div><b>{item.product.name}</b> <span className="meta mono">{item.product.code}</span></div>
          <div style={{ display: "flex", gap: 6 }}>
            <Link className="btn btn--ghost btn--sm" to={`/productos`}>Productos</Link>
            {canLink && <button className="btn btn--danger btn--sm" onClick={unlink} disabled={busy}>Quitar vínculo</button>}
          </div>
        </div>
      ) : canLink ? <>
        <h4 style={{ margin: "14px 0 6px", fontSize: 13 }}>Sugerencias</h4>
        {item.suggestions.length === 0 ? <p className="muted" style={{ fontSize: 13 }}>Ningún producto de tu catálogo se parece por código, modelo o nombre. Buscalo abajo o agregalo a tu catálogo.</p> : (
          <div className="prv-sug">{item.suggestions.map((sg) => (
            <div key={sg.product_id} className="prv-sug__row">
              <div><b>{sg.name}</b><div className="meta mono">{sg.code}{sg.model ? ` · ${sg.model}` : ""}</div><span className={`prv-chip ${sg.reason === "nombre" ? "prv-chip--warn" : "prv-chip--info"}`}>{sg.label}</span></div>
              <button className="btn btn--soft btn--sm" onClick={() => link(sg.product_id)} disabled={busy}>Vincular</button>
            </div>
          ))}</div>
        )}
        <Field label="Buscar en mi catálogo" hint="Nombre, código o modelo del producto propio.">
          <div style={{ display: "flex", gap: 8 }}>
            <div style={{ flex: 1 }}><Lookup value={pick?.label || ""} placeholder="Escribí para buscar…" fetcher={searchProducts} onSelect={(it) => setPick(it)} /></div>
            <button className="btn btn--crimson btn--sm" disabled={!pick || busy} onClick={() => pick && link(pick.id)}>Vincular</button>
          </div>
        </Field>
      </> : null}
    </Modal>
  );
}

function ItemHead({ item }: { item: Detail }) {
  const { allows } = useSession();
  return (
    <div>
      <div style={{ fontWeight: 700 }}>{item.name}</div>
      <div className="meta">{[item.brand, item.model, item.category, item.subcategory].filter(Boolean).join(" · ")}</div>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 8, fontSize: 13 }}>
        <span>Existencia del proveedor: <b className="mono">{item.stock ?? "—"}</b></span>
        {allows("catalog.costos") && <span>Costo: <b className="mono">{money(item.cost, item.currency)}</b> · {crc(item.cost_crc)}</span>}
        <span className="muted">Lista del {fmtDate(item.list_date)}</span>
      </div>
      {item.description && item.description.includes("\n") && <details style={{ marginTop: 8 }}><summary className="meta" style={{ cursor: "pointer" }}>Especificaciones del proveedor</summary><pre style={{ whiteSpace: "pre-wrap", fontSize: 12, fontFamily: "var(--sans)", color: "var(--text-2)" }}>{item.description.split("\n").slice(1).join("\n")}</pre></details>}
    </div>
  );
}

/* ---------- agregar a mi catalogo (precio con margen sobre venta) ---------- */
function AddModal({ item, onClose, onDone }: { item: Detail; onClose: () => void; onDone: () => void }) {
  const { toast } = useSession();
  const pr = item.proposal;
  const [cats, setCats] = useState<Named[]>([]);
  const [form, setForm] = useState({ code: pr?.code || "", name: item.name.slice(0, 200), margin: String(n(pr?.margin_pct ?? 35)), currency: "CRC", category_id: pr?.category_id ? String(pr.category_id) : "" });
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<Named[]>("/categories").then(setCats).catch(() => setCats([])); }, []);
  /* margen SOBRE LA VENTA: precio = costo / (1 - margen). En colones, redondeado hacia arriba a la centena. */
  const m = Number(form.margin) / 100;
  const ok = m >= 0 && m < 0.96 && item.cost != null && n(item.cost) > 0;
  const base = form.currency === "CRC" ? n(item.cost_crc) : (item.currency === "CRC" ? n(item.cost) / n(pr?.fx || 1) : n(item.cost));
  const price = ok ? (form.currency === "CRC" ? Math.ceil(base / (1 - m) / 100) * 100 : Math.round((base / (1 - m)) * 100) / 100) : null;
  const save = async () => {
    setBusy(true);
    try {
      const r = await api<{ product_id: number; code: string }>(`/supplier-catalog/items/${item.id}/catalog`, { method: "POST", json: { code: form.code.trim() || null, name: form.name.trim() || null, margin_pct: Number(form.margin), currency: form.currency, category_id: form.category_id ? Number(form.category_id) : null } });
      toast(`Producto ${r.code} creado y vinculado. Falta el CABYS para poder facturarlo.`);
      onDone();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <Modal wide title="Agregar a mi catálogo" onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" onClick={save} disabled={busy || !ok}>{busy ? <span className="spinner" /> : "Crear producto"}</button></>}>
      <ItemHead item={item} />
      <div className="grid-2" style={{ marginTop: 12 }}>
        <Field label="Código propio" hint="Sugerido: el modelo del fabricante (no cambia si mañana se lo comprás a otro proveedor)."><input className="input input--mono" style={{ textAlign: "left" }} value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} /></Field>
        <Field label="Nombre"><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
        <Field label="Margen sobre la venta %" hint="Margen de la empresa por defecto. 35 % = el costo es el 65 % del precio."><input className="input input--mono" inputMode="decimal" value={form.margin} onChange={(e) => setForm({ ...form, margin: e.target.value })} /></Field>
        <Field label="Moneda del precio"><select className="select" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}><option value="CRC">Colones</option><option value="USD">Dólares</option></select></Field>
        <Field label="Categoría propia" hint="La del proveedor no se copia: tu catálogo tiene sus categorías."><select className="select" value={form.category_id} onChange={(e) => setForm({ ...form, category_id: e.target.value })}><option value="">Sin categoría</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
      </div>
      <div className="prv-price">
        <span className="meta">Precio de venta sin IVA</span>
        <b>{price != null ? fmtMoney(price, form.currency) : "—"}</b>
        <span className="muted" style={{ fontSize: 12.5 }}>= costo {form.currency === "CRC" ? crc(item.cost_crc) : money(base, "USD")} ÷ (1 − {form.margin || 0} %){form.currency === "CRC" ? " · redondeado a la centena" : ""}</span>
      </div>
      <p className="muted" style={{ marginTop: 10, fontSize: 12.5 }}>IVA 13 % y CABYS vacío para completar en Productos → Completar CABYS. No se publica en la tienda hasta que lo actives. Queda vinculado: cada lista nueva actualiza su costo y la existencia del proveedor.</p>
    </Modal>
  );
}

/* ---------- confirmar sugerencias en bloque ---------- */
function SuggestionsModal({ supplierId, onClose, onDone }: { supplierId: number | null; onClose: () => void; onDone: () => void }) {
  const { toast } = useSession();
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const [rows, setRows] = useState<{ item_id: number; code: string; name: string; model: string | null; suggestions: Suggestion[] }[] | null>(null);
  const [sel, setSel] = useState<Record<number, number>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<typeof rows>(`/supplier-catalog/suggestions${supplierId ? `?supplier_id=${supplierId}` : ""}`).then((r) => setRows(r || [])).catch((e) => { toastRef.current(e instanceof Error ? e.message : "Error", "bad"); setRows([]); });
  }, [supplierId]);
  const confirm = async () => {
    const links = Object.entries(sel).map(([item_id, product_id]) => ({ item_id: Number(item_id), product_id }));
    if (!links.length) return;
    setBusy(true);
    try { const r = await api<{ linked: number }>("/supplier-catalog/links", { method: "POST", json: { links } }); toast(`${r.linked} vínculos confirmados`); onDone(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  const count = Object.keys(sel).length;
  return (
    <Modal wide title="Sugerencias de vínculo" onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cerrar</button><button className="btn btn--crimson" onClick={confirm} disabled={busy || !count}>{busy ? <span className="spinner" /> : `Confirmar ${count} vínculo${count === 1 ? "" : "s"}`}</button></>}>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Los vínculos exactos (código del proveedor o modelo) ya se hicieron solos al importar. Estos se parecen por modelo o por nombre: nada se vincula sin que lo marqués.</p>
      {rows === null ? <Loading /> : rows.length === 0 ? <Empty title="Sin sugerencias pendientes" hint="Todo lo que se parece a tu catálogo ya está vinculado." /> : (
        <div className="prv-list" style={{ maxHeight: 460 }}>
          <table className="table">
            <thead><tr><th>Ítem del proveedor</th><th>Producto propio sugerido</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.item_id}>
                <td style={{ minWidth: 220 }}><b style={{ fontWeight: 600 }}>{r.name}</b><div className="meta mono">{r.code}{r.model ? ` · ${r.model}` : ""}</div></td>
                <td>
                  <select className="select select--sm" value={sel[r.item_id] ?? ""} onChange={(e) => { const v = e.target.value; setSel((cur) => { const nx = { ...cur }; if (v) nx[r.item_id] = Number(v); else delete nx[r.item_id]; return nx; }); }}>
                    <option value="">— No vincular —</option>
                    {r.suggestions.map((sg) => <option key={sg.product_id} value={sg.product_id}>{sg.code} · {sg.name.slice(0, 60)} ({sg.label}{sg.already_linked ? ", ya tiene otra oferta" : ""})</option>)}
                  </select>
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Modal>
  );
}
