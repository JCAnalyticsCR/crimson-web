import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmtMoney, type Product } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";

type Tax = { id: number; name: string; rate: number };
const blank = { name: "", code: "", item_type: "producto", price: "0", currency: "CRC", cabys_code: "", description_invoice: "", description_store: "", unit: "Unid", show_on_web: false, min_stock: 0, tax_ids: [] as number[] };

export default function Products() {
  const { toast } = useSession();
  const [params, setParams] = useSearchParams();
  const [items, setItems] = useState<Product[]>([]);
  const [taxes, setTaxes] = useState<Tax[]>([]);
  const [q, setQ] = useState("");
  const [edit, setEdit] = useState<typeof blank & { id?: number } | null>(params.get("nuevo") ? { ...blank } : null);
  const load = useCallback(() => api<{ items: Product[] }>(`/products?limit=50${q ? `&q=${encodeURIComponent(q)}` : ""}`).then((r) => setItems(r.items)), [q]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);
  useEffect(() => { api<Tax[]>("/taxes").then(setTaxes); }, []);

  const save = async () => {
    if (!edit) return;
    try {
      const { id, ...body } = edit;
      await api(id ? `/products/${id}` : "/products", { method: id ? "PUT" : "POST", json: { ...body, price: Number(body.price) } });
      toast("Producto guardado"); setEdit(null); setParams({}); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">05 · Catálogo</div><h1 className="h1">Productos & Servicios</h1></div>
        <div className="page-head__actions"><button className="btn btn--crimson" onClick={() => setEdit({ ...blank, tax_ids: taxes[0] ? [taxes[0].id] : [] })}><Icon d={I.plus} />Crear producto</button></div>
      </div>
      <Card flush>
        <div className="list-head"><div className="search" style={{ maxWidth: 420 }}><Icon d={I.search} size={16} /><input placeholder="Nombre, código o CABYS…" value={q} onChange={(e) => setQ(e.target.value)} /></div><button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} /></button></div>
        {items.length === 0 ? <Empty hint="Creá productos y servicios con su código CABYS e impuesto." /> : (
          <table className="table">
            <thead><tr><th>Código</th><th>Nombre</th><th>Tipo</th><th>CABYS</th><th className="num">Precio</th><th>IVA</th><th>Web</th><th /></tr></thead>
            <tbody>{items.map((p) => (
              <tr key={p.id}><td className="mono muted">{p.code}</td><td style={{ fontWeight: 600 }}>{p.name}</td><td className="muted" style={{ textTransform: "capitalize" }}>{p.item_type}</td><td className="mono muted">{p.cabys_code || "—"}</td><td className="num money">{fmtMoney(p.price, p.currency)}</td><td className="muted">{p.tax_rate ?? "—"}%</td><td>{p.show_on_web ? <span className="badge badge--ok">Sí</span> : <span className="badge badge--muted">No</span>}</td>
                <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => api<Product & { tax_ids: number[]; description_invoice: string | null; description_store: string | null; min_stock: number }>(`/products/${p.id}`).then((f) => setEdit({ id: f.id, name: f.name, code: f.code, item_type: f.item_type, price: String(f.price), currency: f.currency, cabys_code: f.cabys_code || "", description_invoice: f.description_invoice || "", description_store: f.description_store || "", unit: f.unit, show_on_web: f.show_on_web, min_stock: f.min_stock, tax_ids: f.tax_ids }))}>Ver</button></td></tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      {edit && (
        <Modal title={edit.id ? "Producto" : "Nuevo producto"} onClose={() => { setEdit(null); setParams({}); }} wide foot={<><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Guardar</button></>}>
          <div className="grid-3">
            <Field label="Nombre"><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Código"><input className="input" value={edit.code} onChange={(e) => setEdit({ ...edit, code: e.target.value })} /></Field>
            <Field label="Tipo de ítem"><select className="select" value={edit.item_type} onChange={(e) => setEdit({ ...edit, item_type: e.target.value })}><option value="producto">Producto</option><option value="servicio">Servicio</option></select></Field>
            <Field label="Precio"><input className="input input--mono" value={edit.price} onChange={(e) => setEdit({ ...edit, price: e.target.value })} /></Field>
            <Field label="Divisa"><select className="select" value={edit.currency} onChange={(e) => setEdit({ ...edit, currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field>
            <Field label="Impuesto"><select className="select" value={edit.tax_ids[0] ?? ""} onChange={(e) => setEdit({ ...edit, tax_ids: e.target.value ? [Number(e.target.value)] : [] })}><option value="">Sin impuesto</option>{taxes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field>
            <Field label="Código CABYS" hint="13 dígitos (Hacienda)."><input className="input input--mono" style={{ textAlign: "left" }} maxLength={13} value={edit.cabys_code} onChange={(e) => setEdit({ ...edit, cabys_code: e.target.value })} /></Field>
            <Field label="Unidad"><input className="input" value={edit.unit} onChange={(e) => setEdit({ ...edit, unit: e.target.value })} /></Field>
            <Field label="Stock mínimo"><input className="input input--mono" type="number" value={edit.min_stock} onChange={(e) => setEdit({ ...edit, min_stock: Number(e.target.value) })} /></Field>
          </div>
          <Field label="Descripción · Facturación" hint="Larga; va a cotizaciones y facturas."><textarea className="textarea" value={edit.description_invoice} onChange={(e) => setEdit({ ...edit, description_invoice: e.target.value })} /></Field>
          <Field label="Descripción · Comercio electrónico" hint="Corta; va a Links y a la tienda."><textarea className="textarea" style={{ minHeight: 60 }} value={edit.description_store} onChange={(e) => setEdit({ ...edit, description_store: e.target.value })} /></Field>
          <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={edit.show_on_web} onChange={(e) => setEdit({ ...edit, show_on_web: e.target.checked })} />Mostrar en sitio web</label>
        </Modal>
      )}
    </>
  );
}
