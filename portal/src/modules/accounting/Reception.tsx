/* Bandeja de entrada: XML de proveedores -> aceptar / parcial / rechazar ante Hacienda y crear el gasto. */
import { useEffect, useRef, useState } from "react";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Empty, Field, I, Icon, Modal } from "../../ui/components";

type Rec = { id: number; clave: string; consecutive: string | null; issuer_name: string | null; issuer_id: string | null; issue_date: string | null; currency: string; subtotal: string; tax_total: string; total: string; iva_condition: string; action: string | null; hacienda_status: string; expense_id: number | null; created_at: string; has_response: boolean };
type Cat = { id: number; name: string };

export default function Reception() {
  const { toast } = useSession();
  const [items, setItems] = useState<Rec[]>([]);
  const [cats, setCats] = useState<Cat[]>([]);
  const [sel, setSel] = useState<Rec | null>(null);
  const [form, setForm] = useState({ action: "aceptada", iva_condition: "credito", category_id: "", create_expense: true });
  const file = useRef<HTMLInputElement>(null);
  const load = () => { api<Rec[]>("/reception").then(setItems); api<Cat[]>("/expense-categories").then(setCats); };
  useEffect(load, []);

  const upload = async (f: File) => {
    const fd = new FormData();
    fd.append("file", f);
    try { await api("/reception/upload", { method: "POST", body: fd }); toast("XML recibido"); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const respond = async () => {
    if (!sel) return;
    try { await api(`/reception/${sel.id}/respond`, { method: "POST", json: { ...form, category_id: form.category_id ? Number(form.category_id) : null } }); toast("Respuesta registrada"); setSel(null); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">12 · Recepción</div><h1 className="h1">Bandeja de entrada</h1></div>
        <div className="page-head__actions">
          <input ref={file} type="file" accept=".xml,application/xml,text/xml" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          <button className="btn btn--crimson" onClick={() => file.current?.click()}><Icon d={I.plus} />Cargar XML</button>
        </div>
      </div>
      <Card flush extra={<span className="meta">facturas electrónicas de proveedores</span>}>
        {items.length === 0 ? <Empty hint="Cargá el XML que te envía el proveedor: se registra, se responde a Hacienda y se crea el gasto con su IVA acreditable." /> : (
          <table className="table">
            <thead><tr><th>Emisor</th><th>Fecha</th><th>Consecutivo</th><th className="num">Subtotal</th><th className="num">IVA</th><th className="num">Total</th><th>IVA acreditable</th><th>Respuesta</th><th /></tr></thead>
            <tbody>{items.map((r) => (
              <tr key={r.id}>
                <td><b>{r.issuer_name || "—"}</b><div className="meta" style={{ textTransform: "none" }}>{r.issuer_id}</div></td>
                <td className="muted">{fmtDate(r.issue_date)}</td>
                <td className="mono muted">{r.consecutive || r.clave.slice(-10)}</td>
                <td className="num money">{fmtMoney(r.subtotal, r.currency)}</td>
                <td className="num money">{fmtMoney(r.tax_total, r.currency)}</td>
                <td className="num money" style={{ fontWeight: 700 }}>{fmtMoney(r.total, r.currency)}</td>
                <td className="muted">{r.action ? r.iva_condition : "—"}</td>
                <td>{r.action ? <Badge status={r.action === "rechazada" ? "fallido" : "confirmado"} /> : <Badge status="pendiente" />}</td>
                <td className="num" style={{ whiteSpace: "nowrap" }}>
                  {!r.action && <button className="btn btn--crimson btn--sm" onClick={() => { setSel(r); setForm({ action: "aceptada", iva_condition: "credito", category_id: "", create_expense: true }); }}>Responder</button>}
                  {r.has_response && <a className="btn btn--ghost btn--sm" href={`/api/reception/${r.id}/xml/response`}>XML</a>}
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>

      {sel && (
        <Modal title="Responder a Hacienda" onClose={() => setSel(null)} foot={<><button className="btn btn--ghost" onClick={() => setSel(null)}>Cancelar</button><button className="btn btn--crimson" onClick={respond}>Registrar respuesta</button></>}>
          <p className="muted" style={{ fontSize: 13 }}>{sel.issuer_name} · {fmtMoney(sel.total, sel.currency)}<br /><span className="mono" style={{ fontSize: 11 }}>{sel.clave}</span></p>
          <div className="grid-2">
            <Field label="Acción"><select className="select" value={form.action} onChange={(e) => setForm({ ...form, action: e.target.value })}><option value="aceptada">Aceptar</option><option value="parcial">Aceptación parcial</option><option value="rechazada">Rechazar</option></select></Field>
            <Field label="Condición del IVA"><select className="select" value={form.iva_condition} onChange={(e) => setForm({ ...form, iva_condition: e.target.value })}><option value="credito">Genera crédito</option><option value="no_credito">No genera crédito</option><option value="proporcional">Proporcional (prorrata)</option></select></Field>
            <Field label="Categoría del gasto"><select className="select" value={form.category_id} onChange={(e) => setForm({ ...form, category_id: e.target.value })}><option value="">—</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
          </div>
          <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={form.create_expense} onChange={(e) => setForm({ ...form, create_expense: e.target.checked })} />Registrar el gasto en contabilidad</label>
        </Modal>
      )}
    </>
  );
}
