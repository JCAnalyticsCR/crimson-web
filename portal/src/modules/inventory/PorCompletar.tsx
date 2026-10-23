/* Inventarios → Por completar: lo que NO está a la venta en la tienda porque le falta un dato (API /catalog-completar).
   (a) artículos de Eurocomp sin costo que no se pudieron crear; (b) productos sin precio («Consultar» en la tienda).
   Lo que la tienda ya vende aunque le falte foto, descripción o CABYS no aparece aquí. Cada fila abre el cajón para
   completarla; el interruptor "Mostrar en la tienda en línea" es del administrador (catalog.publicar). El costo solo
   llega a quien ve costos: inventario completa sin verlo. Tablet 768 px: la tabla pasa a tarjetas (table--stack). */
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Loading, Modal } from "../../ui/components";
import { GalleryField, type GalleryImage } from "../../ui/MediaPicker";
import { COMPLETAR_EVENT } from "./useCompletarCount";

type Row = {
  key: string; kind: "proveedor" | "producto"; id: number; code: string; name: string; brand: string | null; model: string | null;
  family: string | null; category: string | null; category_id: number | null; category_new: string | null;
  reason: "sin_costo" | "sin_precio"; reason_label: string; missing: string[]; images: GalleryImage[];
  cabys_code: string | null; description_store: string | null; stock: number | null; show_on_web: boolean;
  price: number | string | null; currency: string; cost?: number | string | null; cost_currency?: string; cost_crc?: number | string | null;
};
type Resp = {
  items: Row[]; counts: Record<string, number>; total: number; reasons: Record<string, string>; families: string[]; brands: string[];
  fx: number | string | null; can_publish: boolean; can_create: boolean;
};
type Cat = { id: number; name: string; parent_id: number | null };

const REASON_SHORT: Record<string, string> = { sin_costo: "Sin costo del proveedor", sin_precio: "Sin precio de venta" };
const MISSING: Record<string, string> = { foto: "foto", cabys: "CABYS", descripcion: "descripción", categoria: "categoría" };

/* "Electrodomésticos › Lavadoras": las hijas con la ruta de su padre */
const catLabel = (cats: Cat[], c: Cat) => {
  const byId = new Map(cats.map((x) => [x.id, x]));
  const names = [c.name];
  let cur = c.parent_id ? byId.get(c.parent_id) : undefined;
  for (let n = 0; cur && n < 5; n++) { names.unshift(cur.name); cur = cur.parent_id ? byId.get(cur.parent_id) : undefined; }
  return names.join(" › ");
};

export default function PorCompletar({ onCount }: { onCount?: (n: number) => void }) {
  const { toast, allows } = useSession();
  const [data, setData] = useState<Resp | null>(null);
  const [f, setF] = useState({ reason: "", family: "", brand: "", q: "" });
  const [cats, setCats] = useState<Cat[]>([]);
  const [edit, setEdit] = useState<Row | null>(null);
  const [sel, setSel] = useState<Set<number>>(new Set()); // ids de PRODUCTO (los del proveedor aún no son producto)
  const [busy, setBusy] = useState<number | null>(null);
  const verCostos = allows("catalog.costos");

  const load = useCallback(() => {
    const qs = new URLSearchParams();
    Object.entries(f).forEach(([k, v]) => { if (v) qs.set(k, v); });
    return api<Resp>(`/catalog-completar?${qs}`).then((r) => { setData(r); onCount?.(r.total); window.dispatchEvent(new CustomEvent(COMPLETAR_EVENT, { detail: r.total })); }).catch((e) => toast(e instanceof Error ? e.message : "Error", "bad"));
  }, [f, onCount, toast]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);
  useEffect(() => { api<Cat[]>("/categories").then(setCats).catch(() => setCats([])); }, []);
  const catOpts = useMemo(() => cats.map((c) => ({ id: c.id, label: catLabel(cats, c) })).sort((a, b) => a.label.localeCompare(b.label, "es")), [cats]);

  const canPublish = !!data?.can_publish;
  const marcar = (id: number) => setSel((s0) => { const n = new Set(s0); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const toggleWeb = async (r: Row) => {
    if (!canPublish || r.kind !== "producto") return;
    setBusy(r.id);
    try { await api(`/catalog-completar/products/${r.id}`, { method: "PATCH", json: { show_on_web: !r.show_on_web } }); toast(r.show_on_web ? "Oculto en la tienda" : "Se muestra en la tienda"); await load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(null); }
  };
  const bulkWeb = async (on: boolean) => {
    try {
      const r = await api<{ changed: number }>("/catalog-completar/web", { method: "POST", json: { product_ids: [...sel], show_on_web: on } });
      toast(`${r.changed} producto(s) ${on ? "se muestran" : "ocultos"} en la tienda`); setSel(new Set()); await load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const productRows = (data?.items || []).filter((r) => r.kind === "producto");
  const thumb = (r: Row) => r.images.find((x) => x.main)?.url || r.images[0]?.url;

  return (
    <>
      <p className="muted" style={{ fontSize: 13, margin: 0 }}>
        Artículos que no están a la venta en la tienda en línea porque les falta un dato. Complételos aquí: cuando quedan listos salen de la lista y la tienda se actualiza sola.
      </p>
      {data && (
        <div className="pending">
          <div className={!f.reason ? "hot" : ""} onClick={() => setF({ ...f, reason: "" })} style={{ cursor: "pointer" }}><b>{(data.counts.sin_costo || 0) + (data.counts.sin_precio || 0)}</b><span>Por completar</span></div>
          {Object.keys(REASON_SHORT).map((k) => (
            <div key={k} className={f.reason === k ? "hot" : ""} onClick={() => setF({ ...f, reason: f.reason === k ? "" : k })} style={{ cursor: "pointer" }} title={data.reasons[k]}>
              <b>{data.counts[k] || 0}</b><span>{REASON_SHORT[k]}</span>
            </div>
          ))}
        </div>
      )}
      <Card flush>
        <div className="list-head" style={{ flexWrap: "wrap" }}>
          <div className="search" style={{ maxWidth: 300 }}><Icon d={I.search} size={16} /><input placeholder="Nombre, código o modelo…" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} /></div>
          <select className="select select--sm" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })}><option value="">Todo motivo</option>{Object.entries(REASON_SHORT).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <select className="select select--sm" value={f.family} onChange={(e) => setF({ ...f, family: e.target.value })}><option value="">Toda familia o categoría</option>{(data?.families || []).map((x) => <option key={x}>{x}</option>)}</select>
          <select className="select select--sm" value={f.brand} onChange={(e) => setF({ ...f, brand: e.target.value })}><option value="">Toda marca</option>{(data?.brands || []).map((x) => <option key={x}>{x}</option>)}</select>
          {(f.reason || f.family || f.brand || f.q) && <button className="btn btn--ghost btn--sm" onClick={() => setF({ reason: "", family: "", brand: "", q: "" })}><Icon d={I.x} size={14} />Limpiar</button>}
          <button className="btn btn--ghost btn--sm" onClick={load} style={{ marginLeft: "auto" }} aria-label="Actualizar"><Icon d={I.refresh} /></button>
        </div>
        {canPublish && productRows.length > 0 && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", padding: "8px 18px", background: "var(--bg-2)", borderBottom: "1px solid var(--hair)" }}>
            <label style={{ display: "inline-flex", gap: 6, alignItems: "center", fontSize: 13 }}>
              <input type="checkbox" checked={productRows.every((r) => sel.has(r.id))} onChange={(e) => setSel(e.target.checked ? new Set(productRows.map((r) => r.id)) : new Set())} />Marcar los productos
            </label>
            {sel.size > 0 && <>
              <b style={{ fontSize: 13 }}>{sel.size} marcado(s)</b>
              <button className="btn btn--soft btn--sm" onClick={() => bulkWeb(true)}>Mostrar en la tienda</button>
              <button className="btn btn--ghost btn--sm" onClick={() => bulkWeb(false)}>Ocultar en la tienda</button>
            </>}
          </div>
        )}
        {data === null ? <Loading /> : data.items.length === 0 ? <Empty hint={data.total ? "Nada con esos filtros." : "Todo lo del catálogo tiene lo necesario para venderse en la tienda."} /> : (
          <table className="table table--stack">
            <thead><tr>{canPublish && <th style={{ width: 32 }} />}<th style={{ width: 48 }} /><th>Artículo</th><th>Familia / categoría</th><th>Motivo</th><th>También falta</th>{verCostos && <th className="num">Costo</th>}<th>En la tienda</th><th /></tr></thead>
            <tbody>{data.items.map((r) => (
              <tr key={r.key}>
                {canPublish && <td data-label="Marcar">{r.kind === "producto" ? <input type="checkbox" aria-label={`Marcar ${r.name}`} checked={sel.has(r.id)} onChange={() => marcar(r.id)} /> : <span className="muted" title="Primero hay que crear el producto">—</span>}</td>}
                <td>{thumb(r) ? <span style={{ display: "block", width: 36, height: 36, borderRadius: 8, background: `center/contain no-repeat #fff url("${thumb(r)}")`, border: "1px solid var(--hair)" }} /> : <span style={{ display: "grid", placeItems: "center", width: 36, height: 36, borderRadius: 8, background: "var(--bg-2)", color: "var(--text-3)" }}><Icon d={I.products} size={16} /></span>}</td>
                <td data-label="Artículo" className="stack-full"><b>{r.name}</b><div className="muted mono" style={{ fontSize: 12 }}>{r.code}{r.brand ? ` · ${r.brand}` : ""}{r.kind === "proveedor" ? " · Eurocomp" : ""}{r.stock != null ? ` · existencia ${r.stock}` : ""}</div></td>
                <td data-label="Familia / categoría" className="muted" style={{ fontSize: 13 }}>{r.category || r.family || "—"}</td>
                <td data-label="Motivo"><span className={`badge ${r.reason === "sin_costo" ? "badge--warn" : "badge--bad"}`} title={r.reason_label}>{REASON_SHORT[r.reason]}</span></td>
                <td data-label="También falta" className="muted" style={{ fontSize: 12.5 }}>{r.missing.length ? r.missing.map((m) => MISSING[m] || m).join(", ") : "—"}</td>
                {verCostos && <td data-label="Costo" className="num mono">{r.cost != null ? fmtMoney(r.cost, r.cost_currency || "USD") : <span className="muted">—</span>}</td>}
                <td data-label="En la tienda">
                  {r.kind === "producto"
                    ? <button className={`switch${r.show_on_web ? " is-on" : ""}`} disabled={!canPublish || busy === r.id} onClick={() => toggleWeb(r)} title={canPublish ? (r.show_on_web ? "Se muestra en la tienda · clic para ocultarlo" : "Oculto · clic para mostrarlo") : "Solo el administrador decide qué se muestra en línea"} aria-label="Mostrar en la tienda en línea" aria-pressed={r.show_on_web}><i /></button>
                    : <span className="muted" style={{ fontSize: 12.5 }}>Aún no es producto</span>}
                </td>
                <td className="num stack-actions"><button className="btn btn--crimson btn--sm" onClick={() => setEdit(r)}>Completar</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      {edit && <Cajon row={edit} cats={catOpts} canPublish={canPublish} canCreate={!!data?.can_create} verCostos={verCostos} onClose={() => setEdit(null)} onDone={() => { setEdit(null); load(); }} onCatCreated={(c) => setCats((xs) => [...xs, c])} />}
    </>
  );
}

function Cajon({ row, cats, canPublish, canCreate, verCostos, onClose, onDone, onCatCreated }: {
  row: Row; cats: { id: number; label: string }[]; canPublish: boolean; canCreate: boolean; verCostos: boolean;
  onClose: () => void; onDone: () => void; onCatCreated: (c: Cat) => void;
}) {
  const { toast, allows } = useSession();
  const [v, setV] = useState({
    price: row.price != null && Number(row.price) > 0 ? String(row.price) : "", currency: row.currency || "CRC", cost: "",
    cabys_code: row.cabys_code || "", description_store: row.description_store || "", category_id: row.category_id ? String(row.category_id) : "",
    images: row.images || [], show_on_web: row.kind === "producto" ? row.show_on_web : canPublish && row.show_on_web,
  });
  const [newCat, setNewCat] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const precio = Number(v.price.replace(",", "."));
  const okPrecio = v.price.trim() !== "" && Number.isFinite(precio) && precio > 0;
  const okCabys = v.cabys_code === "" || /^\d{13}$/.test(v.cabys_code);
  const verPrecio = allows("catalog.precios");

  const crearCat = async () => {
    if (!newCat?.trim()) return;
    try { const c = await api<Cat>("/categories", { method: "POST", json: { name: newCat.trim() } }); onCatCreated(c); setV({ ...v, category_id: String(c.id) }); setNewCat(null); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const guardar = async () => {
    if (!okPrecio && row.kind === "proveedor") { toast("Escriba el precio de venta", "bad"); return; }
    if (!okCabys) { toast("El CABYS lleva 13 dígitos", "bad"); return; }
    setSaving(true);
    try {
      const comun = {
        cabys_code: v.cabys_code || null, description_store: v.description_store, category_id: v.category_id ? Number(v.category_id) : null,
        images: v.images.map((x) => ({ url: x.url, main: !!x.main })),
      };
      if (row.kind === "proveedor") {
        const body: Record<string, unknown> = { ...comun, price: precio, currency: v.currency, show_on_web: canPublish && v.show_on_web };
        if (verCostos && v.cost.trim()) body.cost = Number(v.cost.replace(",", "."));
        await api(`/catalog-completar/items/${row.id}/crear`, { method: "POST", json: body });
        toast(canPublish && v.show_on_web ? "Producto creado y publicado en la tienda" : "Producto creado (no se muestra en la tienda)");
      } else {
        const body: Record<string, unknown> = { ...comun };
        if (verPrecio && okPrecio) body.price = precio;
        if (canPublish) body.show_on_web = v.show_on_web;
        await api(`/catalog-completar/products/${row.id}`, { method: "PATCH", json: body });
        toast(okPrecio ? "Producto completado" : "Guardado: todavía le falta el precio");
      }
      onDone();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setSaving(false); }
  };
  const descartar = async () => {
    try { await api(`/catalog-completar/items/${row.id}/descartar`, { method: "POST" }); toast("Descartado: no se venderá ni se volverá a crear"); onDone(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const crea = row.kind === "proveedor";
  return (
    <Modal title={row.name} onClose={onClose} wide foot={<>
      {crea && <button className="btn btn--ghost" style={{ marginRight: "auto" }} onClick={descartar} title="No vender este artículo: sale de la lista y la sincronización no lo crea">No vender</button>}
      <button className="btn btn--ghost" onClick={onClose}>Cancelar</button>
      {crea
        ? <button className="btn btn--crimson" disabled={saving || !okPrecio || !canCreate} onClick={guardar}>{canPublish && v.show_on_web ? "Crear y publicar" : "Crear producto"}</button>
        : <button className="btn btn--crimson" disabled={saving} onClick={guardar}>Guardar</button>}
    </>}>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>{row.reason_label}. <span className="mono">{row.code}</span>{row.brand ? ` · ${row.brand}` : ""}{row.model ? ` · ${row.model}` : ""}</p>
      <div className="grid-3">
        {verPrecio && <Field label="Precio de venta (sin IVA)" hint={crea ? "Obligatorio. La sincronización con Eurocomp nunca lo cambia." : "Con precio el producto se puede comprar en la tienda."}>
          <input className="input input--mono" inputMode="decimal" autoFocus value={v.price} onChange={(e) => setV({ ...v, price: e.target.value })} placeholder="0" />
        </Field>}
        {crea && <Field label="Divisa"><select className="select" value={v.currency} onChange={(e) => setV({ ...v, currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field>}
        {crea && verCostos && <Field label="Costo (opcional, USD)" hint="Eurocomp no lo publica. Si lo carga, se calcula el margen."><input className="input input--mono" inputMode="decimal" value={v.cost} onChange={(e) => setV({ ...v, cost: e.target.value })} /></Field>}
        {!crea && verCostos && row.cost_crc != null && <Field label="Costo y margen"><span className="mono" style={{ fontSize: 13 }}>{fmtMoney(row.cost_crc, "CRC")}{okPrecio && v.currency === "CRC" ? ` · margen ${(((precio - Number(row.cost_crc)) / precio) * 100).toFixed(1)} %` : ""}</span></Field>}
        <Field label="Código CABYS" hint={row.cabys_code ? "Sugerido por Eurocomp." : "13 dígitos (Hacienda). Se puede completar después."}><input className="input input--mono" style={{ textAlign: "left" }} maxLength={13} value={v.cabys_code} onChange={(e) => setV({ ...v, cabys_code: e.target.value.replace(/\D/g, "") })} /></Field>
        <Field label="Categoría" hint={!v.category_id && row.category_new ? `Vacía: se crea «${row.category_new}».` : undefined}>
          {newCat === null
            ? <select className="select" value={v.category_id} onChange={(e) => e.target.value === "__new" ? setNewCat("") : setV({ ...v, category_id: e.target.value })}><option value="">{row.category_new ? `Automática (${row.category_new})` : "Sin categoría"}</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}{allows("catalog.crear") && <option value="__new">+ Nueva categoría…</option>}</select>
            : <div style={{ display: "flex", gap: 6 }}><input className="input" autoFocus placeholder="Nombre" value={newCat} onChange={(e) => setNewCat(e.target.value)} onKeyDown={(e) => e.key === "Enter" && crearCat()} /><button className="btn btn--soft btn--sm" onClick={crearCat}>Crear</button><button className="btn btn--ghost btn--sm" onClick={() => setNewCat(null)} aria-label="Cancelar"><Icon d={I.x} size={14} /></button></div>}
        </Field>
      </div>
      <Field label="Descripción · tienda" hint="Corta; va a la ficha de la tienda y a los links."><textarea className="textarea" style={{ minHeight: 70 }} value={v.description_store} onChange={(e) => setV({ ...v, description_store: e.target.value })} /></Field>
      <Field label="Fotos" hint={row.images.length && crea ? "Las de Eurocomp ya vienen; puede quitar o subir otras." : "La principal sale en la tienda."}><GalleryField value={v.images} onChange={(images) => setV({ ...v, images })} /></Field>
      <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 14, marginTop: 6 }} title={canPublish ? undefined : "Solo el administrador decide qué se muestra en línea"}>
        <button type="button" className={`switch${v.show_on_web ? " is-on" : ""}`} disabled={!canPublish} onClick={() => setV({ ...v, show_on_web: !v.show_on_web })} aria-pressed={v.show_on_web} aria-label="Mostrar en la tienda en línea"><i /></button>
        Mostrar en la tienda en línea{!canPublish && <span className="muted" style={{ fontSize: 12.5 }}> · lo decide el administrador</span>}
      </label>
      {!crea && !verPrecio && <p className="muted" style={{ fontSize: 12.5 }}>Su rol no ve precios: el precio lo pone el administrador.</p>}
    </Modal>
  );
}
