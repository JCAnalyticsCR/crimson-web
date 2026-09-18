import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmtMoney, type Product } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import { GalleryField, type GalleryImage } from "../../ui/MediaPicker";

type Tax = { id: number; name: string; rate: number };
type Named = { id: number; name: string };
type Variant = { id?: number; name: string; code: string; price: string; active: boolean; options: Record<string, string> };
type Full = Product & {
  tax_ids: number[]; description_invoice: string | null; description_store: string | null; min_stock: number; images: GalleryImage[];
  supplier_id: number | null; weight_kg: number | null; registration_number: string | null; cabys_description: string | null; tariff_code: string | null; active: boolean;
};

const blank = {
  name: "", code: "", item_type: "producto", price: "0", currency: "CRC", cabys_code: "", description_invoice: "", description_store: "", unit: "Unid",
  show_on_web: false, min_stock: 0, tax_ids: [] as number[], category_id: "" as string, supplier_id: "" as string, weight_kg: "", images: [] as GalleryImage[],
  registration_number: "", tariff_code: "",
};
type Edit = typeof blank & { id?: number };

const TABS = [["general", "General"], ["imagenes", "Imágenes"], ["variantes", "Variantes"]] as const;

export default function Products() {
  const { toast, allows } = useSession();
  const [params, setParams] = useSearchParams();
  const [items, setItems] = useState<(Product & { images?: GalleryImage[] })[]>([]);
  const [taxes, setTaxes] = useState<Tax[]>([]);
  const [cats, setCats] = useState<Named[]>([]);
  const [suppliers, setSuppliers] = useState<Named[]>([]);
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<string>("general");
  const [edit, setEdit] = useState<Edit | null>(params.get("nuevo") ? { ...blank } : null);
  const [variants, setVariants] = useState<Variant[]>([]);
  const [newCat, setNewCat] = useState<string | null>(null);

  const load = useCallback(() => api<{ items: Product[] }>(`/products?limit=50${q ? `&q=${encodeURIComponent(q)}` : ""}`).then((r) => setItems(r.items)), [q]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);
  useEffect(() => {
    api<Tax[]>("/taxes").then(setTaxes);
    api<Named[]>("/categories").then(setCats).catch(() => setCats([]));
    api<Named[]>("/suppliers").then(setSuppliers).catch(() => setSuppliers([]));
  }, []);

  const open = async (id: number) => {
    const f = await api<Full>(`/products/${id}`);
    setEdit({
      id: f.id, name: f.name, code: f.code, item_type: f.item_type, price: String(f.price), currency: f.currency, cabys_code: f.cabys_code || "",
      description_invoice: f.description_invoice || "", description_store: f.description_store || "", unit: f.unit, show_on_web: f.show_on_web, min_stock: f.min_stock,
      tax_ids: f.tax_ids, category_id: f.category_id ? String(f.category_id) : "", supplier_id: f.supplier_id ? String(f.supplier_id) : "",
      weight_kg: f.weight_kg != null ? String(f.weight_kg) : "", images: f.images || [], registration_number: f.registration_number || "", tariff_code: f.tariff_code || "",
    });
    setTab("general");
    api<(Omit<Variant, "price"> & { price: string | number | null })[]>(`/products/${id}/variants`).then((vs) => setVariants(vs.filter((v) => v.active).map((v) => ({ ...v, price: v.price == null ? "" : String(v.price) }))));
  };

  const save = async () => {
    if (!edit) return;
    try {
      const { id, ...b } = edit;
      const body = {
        ...b, price: Number(b.price), category_id: b.category_id ? Number(b.category_id) : null, supplier_id: b.supplier_id ? Number(b.supplier_id) : null,
        weight_kg: b.weight_kg ? Number(b.weight_kg) : null, cabys_code: b.cabys_code || null, registration_number: b.registration_number || null, tariff_code: b.tariff_code || null,
      };
      const saved = await api<{ id: number }>(id ? `/products/${id}` : "/products", { method: id ? "PUT" : "POST", json: body });
      if (id || variants.length) {
        await api(`/products/${saved.id}/variants`, { method: "PUT", json: variants.map((v) => ({ id: v.id, name: v.name, code: v.code, price: v.price === "" ? null : Number(v.price), options: v.options, active: v.active })) });
      }
      toast("Producto guardado"); setEdit(null); setVariants([]); setParams({}); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const copyLink = async () => {
    if (!edit?.id) return;
    try {
      const r = await api<{ url: string; ready: boolean; problems: string[] }>(`/products/${edit.id}/link`);
      await navigator.clipboard.writeText(r.url);
      toast(r.problems.length ? `Link copiado. Ojo: ${r.problems[0]}` : "Link del producto copiado", r.ready ? "ok" : "bad");
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo copiar", "bad"); }
  };

  const createCat = async () => {
    if (!newCat?.trim() || !edit) return;
    try { const c = await api<Named>("/categories", { method: "POST", json: { name: newCat.trim() } }); setCats([...cats, c]); setEdit({ ...edit, category_id: String(c.id) }); setNewCat(null); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const canEdit = edit?.id ? allows("catalog.editar") : allows("catalog.crear");
  const setV = (i: number, patch: Partial<Variant>) => setVariants(variants.map((v, j) => (j === i ? { ...v, ...patch } : v)));
  const thumb = (p: { images?: GalleryImage[] }) => (p.images || []).find((x) => x.main)?.url || p.images?.[0]?.url;

  return (
    <>
      <div className="page-head">
        <div><div className="meta">05 · Catálogo</div><h1 className="h1">Productos & Servicios</h1></div>
        <div className="page-head__actions">{allows("catalog.crear") && <button className="btn btn--crimson" onClick={() => { setEdit({ ...blank, tax_ids: taxes[0] ? [taxes[0].id] : [] }); setVariants([]); setTab("general"); }}><Icon d={I.plus} />Crear producto</button>}</div>
      </div>
      <Card flush>
        <div className="list-head"><div className="search" style={{ maxWidth: 420 }}><Icon d={I.search} size={16} /><input placeholder="Nombre, código o CABYS…" value={q} onChange={(e) => setQ(e.target.value)} /></div><button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} /></button></div>
        {items.length === 0 ? <Empty hint="Creá productos y servicios con su código CABYS e impuesto." /> : (
          <table className="table">
            <thead><tr><th style={{ width: 48 }} /><th>Código</th><th>Nombre</th><th>Tipo</th><th>CABYS</th><th className="num">Precio</th><th>IVA</th><th>Web</th><th /></tr></thead>
            <tbody>{items.map((p) => (
              <tr key={p.id}>
                <td>{thumb(p) ? <span style={{ display: "block", width: 36, height: 36, borderRadius: 8, background: `center/cover no-repeat url("${thumb(p)}")`, border: "1px solid var(--hair)" }} /> : <span style={{ display: "grid", placeItems: "center", width: 36, height: 36, borderRadius: 8, background: "var(--bg-2)", color: "var(--text-3)" }}><Icon d={I.products} size={16} /></span>}</td>
                <td className="mono muted">{p.code}</td><td style={{ fontWeight: 600 }}>{p.name}</td><td className="muted" style={{ textTransform: "capitalize" }}>{p.item_type}</td><td className="mono muted">{p.cabys_code || "—"}</td><td className="num money">{fmtMoney(p.price, p.currency)}</td><td className="muted">{p.tax_rate ?? "—"}%</td><td>{p.show_on_web ? <span className="badge badge--ok">Sí</span> : <span className="badge badge--muted">No</span>}</td>
                <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => open(p.id)}>Ver</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      {edit && (
        <Modal title={edit.id ? edit.name || "Producto" : "Nuevo producto"} onClose={() => { setEdit(null); setParams({}); }} wide foot={<>
          {edit.id && <button className="btn btn--ghost" style={{ marginRight: "auto" }} onClick={copyLink} title="Link directo al producto en la tienda"><Icon d={I.link} />Copiar link</button>}
          <button className="btn btn--ghost" onClick={() => setEdit(null)}>{canEdit ? "Cancelar" : "Cerrar"}</button>{canEdit && <button className="btn btn--crimson" onClick={save}>Guardar</button>}
        </>}>
          {!canEdit && <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Solo lectura: tu rol puede consultar el catálogo pero no modificarlo.</p>}
          <div className="tabs" style={{ marginBottom: 14 }}>{TABS.map(([k, l]) => <button key={k} className={tab === k ? "is-active" : ""} onClick={() => setTab(k)}>{l}{k === "variantes" && variants.length ? ` · ${variants.length}` : ""}{k === "imagenes" && edit.images.length ? ` · ${edit.images.length}` : ""}</button>)}</div>

          {tab === "general" && <>
            <div className="grid-3">
              <Field label="Nombre"><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
              <Field label="Código"><input className="input" value={edit.code} onChange={(e) => setEdit({ ...edit, code: e.target.value })} /></Field>
              <Field label="Tipo de ítem"><select className="select" value={edit.item_type} onChange={(e) => setEdit({ ...edit, item_type: e.target.value })}><option value="producto">Producto</option><option value="servicio">Servicio</option></select></Field>
              <Field label="Precio (sin IVA)"><input className="input input--mono" value={edit.price} onChange={(e) => setEdit({ ...edit, price: e.target.value })} /></Field>
              <Field label="Divisa"><select className="select" value={edit.currency} onChange={(e) => setEdit({ ...edit, currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field>
              <Field label="Impuesto"><select className="select" value={edit.tax_ids[0] ?? ""} onChange={(e) => setEdit({ ...edit, tax_ids: e.target.value ? [Number(e.target.value)] : [] })}><option value="">Sin impuesto</option>{taxes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field>
              <Field label="Código CABYS" hint="13 dígitos (Hacienda)."><input className="input input--mono" style={{ textAlign: "left" }} maxLength={13} value={edit.cabys_code} onChange={(e) => setEdit({ ...edit, cabys_code: e.target.value })} /></Field>
              <Field label="Unidad"><input className="input" value={edit.unit} onChange={(e) => setEdit({ ...edit, unit: e.target.value })} /></Field>
              <Field label="Stock mínimo"><input className="input input--mono" type="number" value={edit.min_stock} onChange={(e) => setEdit({ ...edit, min_stock: Number(e.target.value) })} /></Field>
              <Field label="Categoría">
                {newCat === null
                  ? <select className="select" value={edit.category_id} onChange={(e) => e.target.value === "__new" ? setNewCat("") : setEdit({ ...edit, category_id: e.target.value })}><option value="">Sin categoría</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}<option value="__new">+ Nueva categoría…</option></select>
                  : <div style={{ display: "flex", gap: 6 }}><input className="input" autoFocus placeholder="Nombre" value={newCat} onChange={(e) => setNewCat(e.target.value)} onKeyDown={(e) => e.key === "Enter" && createCat()} /><button className="btn btn--soft btn--sm" onClick={createCat}>Crear</button><button className="btn btn--ghost btn--sm" onClick={() => setNewCat(null)}><Icon d={I.x} size={14} /></button></div>}
              </Field>
              <Field label="Proveedor"><select className="select" value={edit.supplier_id} onChange={(e) => setEdit({ ...edit, supplier_id: e.target.value })}><option value="">—</option>{suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
              <Field label="Peso (kg)" hint="Para envíos por peso."><input className="input input--mono" value={edit.weight_kg} onChange={(e) => setEdit({ ...edit, weight_kg: e.target.value })} /></Field>
              <Field label="Partida arancelaria"><input className="input input--mono" style={{ textAlign: "left" }} value={edit.tariff_code} onChange={(e) => setEdit({ ...edit, tariff_code: e.target.value })} /></Field>
              <Field label="N.º de registro" hint="Permisos sanitarios u otros."><input className="input" value={edit.registration_number} onChange={(e) => setEdit({ ...edit, registration_number: e.target.value })} /></Field>
            </div>
            <Field label="Descripción · Facturación" hint="Larga; va a cotizaciones y facturas."><textarea className="textarea" value={edit.description_invoice} onChange={(e) => setEdit({ ...edit, description_invoice: e.target.value })} /></Field>
            <Field label="Descripción · Comercio electrónico" hint="Corta; va a Links y a la tienda."><textarea className="textarea" style={{ minHeight: 60 }} value={edit.description_store} onChange={(e) => setEdit({ ...edit, description_store: e.target.value })} /></Field>
            <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={edit.show_on_web} onChange={(e) => setEdit({ ...edit, show_on_web: e.target.checked })} />Mostrar en sitio web</label>
          </>}

          {tab === "imagenes" && <>
            <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>La imagen principal aparece en la tienda, en el POS y en los links de producto.</p>
            <GalleryField value={edit.images} onChange={(images) => setEdit({ ...edit, images })} />
          </>}

          {tab === "variantes" && <>
            <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Color, resolución, tamaño… Cada variante tiene su código; el precio vacío usa el del producto. Las existencias se llevan a nivel de producto.</p>
            {variants.length > 0 && (
              <table className="table">
                <thead><tr><th>Nombre</th><th>Código</th><th className="num">Precio</th><th>Activa</th><th /></tr></thead>
                <tbody>{variants.map((v, i) => (
                  <tr key={v.id ?? `n${i}`}>
                    <td><input className="input" value={v.name} placeholder="Blanca / 4MP" onChange={(e) => setV(i, { name: e.target.value })} /></td>
                    <td><input className="input input--mono" style={{ textAlign: "left" }} value={v.code} placeholder={`${edit.code}-${i + 1}`} onChange={(e) => setV(i, { code: e.target.value })} /></td>
                    <td className="num"><input className="input input--mono" value={v.price} placeholder={String(edit.price)} onChange={(e) => setV(i, { price: e.target.value })} style={{ maxWidth: 140 }} /></td>
                    <td><input type="checkbox" checked={v.active} onChange={(e) => setV(i, { active: e.target.checked })} /></td>
                    <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setVariants(variants.filter((_, j) => j !== i))} title="Quitar"><Icon d={I.x} size={14} /></button></td>
                  </tr>
                ))}</tbody>
              </table>
            )}
            <button className="btn btn--soft btn--sm" style={{ marginTop: 10 }} onClick={() => setVariants([...variants, { name: "", code: `${edit.code || "VAR"}-${variants.length + 1}`, price: "", active: true, options: {} }])}><Icon d={I.plus} />Agregar variante</button>
          </>}
        </Modal>
      )}
    </>
  );
}
