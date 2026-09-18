/* Recurrencias: plantilla + frecuencia -> factura automatica en la fecha (worker) o manual con "Generar ahora". */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, type Customer, type Product } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Empty, Field, I, Icon, Modal } from "../../ui/components";

type Rec = { id: number; name: string; customer_id: number; customer: string | null; frequency: string; next_date: string; end_date: string | null; auto_send: boolean; active: boolean; runs: number; last_invoice_id: number | null; template: { lines: { product_id: number | null; name?: string; quantity: string }[] } };

export default function Recurrences() {
  const { toast } = useSession();
  const [items, setItems] = useState<Rec[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [edit, setEdit] = useState<{ id?: number; name: string; customer_id: string; frequency: string; next_date: string; end_date: string; auto_send: boolean; active: boolean; lines: { product_id: string; quantity: string }[] } | null>(null);
  const load = () => { api<Rec[]>("/recurrences").then(setItems); api<{ items: Customer[] }>("/customers?limit=100").then((r) => setCustomers(r.items)); api<{ items: Product[] }>("/products?limit=100").then((r) => setProducts(r.items)); };
  useEffect(load, []);

  const save = async () => {
    if (!edit) return;
    try {
      const { id, lines, ...b } = edit;
      const body = { ...b, customer_id: Number(b.customer_id), end_date: b.end_date || null, template: { lines: lines.filter((l) => l.product_id).map((l) => ({ product_id: Number(l.product_id), quantity: l.quantity || "1" })) } };
      await api(id ? `/recurrences/${id}` : "/recurrences", { method: id ? "PUT" : "POST", json: body });
      toast("Recurrencia guardada"); setEdit(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const run = async (r: Rec) => { try { const x = await api<{ number: string }>(`/recurrences/${r.id}/run`, { method: "POST" }); toast(`Factura ${x.number} generada`); load(); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">02 · Facturación</div><h1 className="h1">Recurrencias</h1></div>
        <div className="page-head__actions"><button className="btn btn--crimson" onClick={() => setEdit({ name: "", customer_id: "", frequency: "mensual", next_date: new Date().toISOString().slice(0, 10), end_date: "", auto_send: true, active: true, lines: [{ product_id: "", quantity: "1" }] })}><Icon d={I.plus} />Nueva recurrencia</button></div>
      </div>
      <Card flush>
        {items.length === 0 ? <Empty hint="Contratos de mantenimiento, monitoreo mensual… se facturan solos en la fecha programada." /> : (
          <table className="table"><thead><tr><th>Nombre</th><th>Cliente</th><th>Frecuencia</th><th>Próxima</th><th className="num">Generadas</th><th>Última</th><th>Estado</th><th /></tr></thead>
            <tbody>{items.map((r) => <tr key={r.id}><td style={{ fontWeight: 600 }}>{r.name}</td><td>{r.customer}</td><td className="muted" style={{ textTransform: "capitalize" }}>{r.frequency}</td><td className="muted">{fmtDate(r.next_date)}</td><td className="num mono">{r.runs}</td><td>{r.last_invoice_id ? <Link className="row-link mono" to={`/facturas/${r.last_invoice_id}`}>#{r.last_invoice_id}</Link> : "—"}</td><td><Badge status={r.active ? "confirmado" : "anulada"} /></td>
              <td className="num" style={{ whiteSpace: "nowrap" }}><button className="btn btn--soft btn--sm" onClick={() => run(r)}>Generar ahora</button> <button className="btn btn--ghost btn--sm" onClick={() => setEdit({ id: r.id, name: r.name, customer_id: String(r.customer_id), frequency: r.frequency, next_date: r.next_date, end_date: r.end_date || "", auto_send: r.auto_send, active: r.active, lines: r.template.lines.map((l) => ({ product_id: String(l.product_id ?? ""), quantity: String(Number(l.quantity)) })) })}>Ver</button></td></tr>)}</tbody></table>
        )}
      </Card>
      {edit && (
        <Modal title={edit.id ? "Recurrencia" : "Nueva recurrencia"} onClose={() => setEdit(null)} wide foot={<><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Guardar</button></>}>
          <div className="grid-2">
            <Field label="Nombre"><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Cliente"><select className="select" value={edit.customer_id} onChange={(e) => setEdit({ ...edit, customer_id: e.target.value })}><option value="">Seleccione…</option>{customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
            <Field label="Frecuencia"><select className="select" value={edit.frequency} onChange={(e) => setEdit({ ...edit, frequency: e.target.value })}><option value="semanal">Semanal</option><option value="quincenal">Quincenal</option><option value="mensual">Mensual</option><option value="anual">Anual</option></select></Field>
            <Field label="Próxima fecha"><input className="input" type="date" value={edit.next_date} onChange={(e) => setEdit({ ...edit, next_date: e.target.value })} /></Field>
            <Field label="Fecha fin (opcional)"><input className="input" type="date" value={edit.end_date} onChange={(e) => setEdit({ ...edit, end_date: e.target.value })} /></Field>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, justifyContent: "flex-end", fontSize: 13 }}>
              <label style={{ display: "flex", gap: 10 }}><input type="checkbox" checked={edit.auto_send} onChange={(e) => setEdit({ ...edit, auto_send: e.target.checked })} />Enviar al cliente al generar</label>
              <label style={{ display: "flex", gap: 10 }}><input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} />Activa</label>
            </div>
          </div>
          <div className="meta">Líneas de la plantilla</div>
          {edit.lines.map((l, i) => (
            <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 100px 40px", gap: 8 }}>
              <select className="select" value={l.product_id} onChange={(e) => setEdit({ ...edit, lines: edit.lines.map((x, k) => (k === i ? { ...x, product_id: e.target.value } : x)) })}><option value="">Producto o servicio…</option>{products.map((p) => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}</select>
              <input className="input input--mono" value={l.quantity} onChange={(e) => setEdit({ ...edit, lines: edit.lines.map((x, k) => (k === i ? { ...x, quantity: e.target.value } : x)) })} />
              <button className="x" onClick={() => setEdit({ ...edit, lines: edit.lines.filter((_, k) => k !== i) })}><Icon d={I.x} size={14} /></button>
            </div>
          ))}
          <button className="btn btn--ghost btn--sm" style={{ alignSelf: "flex-start" }} onClick={() => setEdit({ ...edit, lines: [...edit.lines, { product_id: "", quantity: "1" }] })}><Icon d={I.plus} />Línea</button>
        </Modal>
      )}
    </>
  );
}
