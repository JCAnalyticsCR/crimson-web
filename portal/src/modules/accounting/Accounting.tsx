/* Contabilidad: gastos (con IVA acreditable), categorias, cuentas bancarias, resultado del periodo. */
import { useEffect, useState } from "react";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import AuthLink from "../../ui/AuthLink";
import { Dropzone } from "../../ui/MediaPicker";

type Cat = { id: number; name: string };
type Bank = { id: number; name: string; bank: string | null; currency: string; number: string | null };
type Sup = { id: number; name: string; tax_id: string | null; email: string | null; phone: string | null };
type Exp = { id: number; category_id: number | null; category: string | null; supplier_id: number | null; attachment_url: string | null; description: string; date: string; currency: string; subtotal: string; tax_rate: string; tax_amount: string; total: string; iva_credit: string; bank_account_id: number | null; reference: string | null; status: string };
type Res = { rows: (string | number)[][]; totals: { resultado: string } };
const blank = { category_id: "", supplier_id: "", attachment_url: "", description: "", date: new Date().toISOString().slice(0, 10), currency: "CRC", subtotal: "", tax_rate: "13", iva_credit: "credito", bank_account_id: "", reference: "", status: "registrado" };

export default function Accounting() {
  const { toast } = useSession();
  const [cats, setCats] = useState<Cat[]>([]);
  const [banks, setBanks] = useState<Bank[]>([]);
  const [items, setItems] = useState<Exp[]>([]);
  const [res, setRes] = useState<Res | null>(null);
  const [edit, setEdit] = useState<(typeof blank & { id?: number }) | null>(null);
  const [newCat, setNewCat] = useState("");
  const [sups, setSups] = useState<Sup[]>([]);
  const [sup, setSup] = useState<(Omit<Sup, "id"> & { id?: number }) | null>(null);
  const load = () => { api<Sup[]>("/suppliers").then(setSups).catch(() => setSups([])); api<Cat[]>("/expense-categories").then(setCats); api<Bank[]>("/settings/bank-accounts").then(setBanks); api<Exp[]>("/expenses").then(setItems); api<Res>("/reports/resultados").then(setRes); };
  useEffect(load, []);

  const save = async () => {
    if (!edit) return;
    try {
      const { id, ...b } = edit;
      await api(id ? `/expenses/${id}` : "/expenses", { method: id ? "PUT" : "POST", json: { ...b, supplier_id: b.supplier_id ? Number(b.supplier_id) : null, attachment_url: b.attachment_url || null, category_id: b.category_id ? Number(b.category_id) : null, bank_account_id: b.bank_account_id ? Number(b.bank_account_id) : null, subtotal: Number(b.subtotal || 0), tax_rate: Number(b.tax_rate) } });
      toast("Gasto guardado"); setEdit(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const saveSup = async () => {
    if (!sup) return;
    const { id, ...b } = sup;
    try {
      await api(id ? `/suppliers/${id}` : "/suppliers", { method: id ? "PUT" : "POST", json: { ...b, tax_id: b.tax_id || null, email: b.email || null, phone: b.phone || null } });
      toast("Proveedor guardado"); setSup(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const addCat = async () => { if (!newCat.trim()) return; await api("/expense-categories", { method: "POST", json: { name: newCat.trim() } }); setNewCat(""); load(); };
  const subtotal = Number(edit?.subtotal || 0), iva = subtotal * Number(edit?.tax_rate || 0) / 100;

  return (
    <>
      <div className="page-head">
        <div><div className="meta">07 · Contabilidad</div><h1 className="h1">Gastos y control</h1></div>
        <div className="page-head__actions">
          <AuthLink path="/reports/gastos?format=xlsx" download="gastos.xlsx"><Icon d={I.reports} />Excel</AuthLink>
          <button className="btn btn--crimson" onClick={() => setEdit({ ...blank })}><Icon d={I.plus} />Registrar gasto</button>
        </div>
      </div>

      {res && (
        <div className="kpi">
          <div className="kpi__card"><span className="kpi__corner" /><div className="kpi__label">Resultado del mes (sin IVA)</div><div className="kpi__value">{fmtMoney(res.totals.resultado)}</div><div className="kpi__sub">{res.rows.map((r) => <span key={String(r[0])}>{r[0]} <b className="money">{fmtMoney(r[1])}</b></span>)}</div></div>
          <div className="kpi__card kpi__card--light"><span className="kpi__corner" /><div className="kpi__label">Categorías de gasto</div><div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "12px 0" }}>{cats.map((c) => <span key={c.id} className="badge badge--muted" style={{ paddingLeft: 9 }}>{c.name}</span>)}</div><div style={{ display: "flex", gap: 8 }}><input className="input" placeholder="Nueva categoría" value={newCat} onChange={(e) => setNewCat(e.target.value)} style={{ height: 34 }} /><button className="btn btn--soft btn--sm" onClick={addCat}>Agregar</button></div></div>
        </div>
      )}

      <Card title="Gastos recientes" flush>
        {items.length === 0 ? <Empty hint="Registrá compras y gastos; el IVA acreditable alimenta el reporte de IVA y la prorrata." /> : (
          <table className="table"><thead><tr><th>Fecha</th><th>Descripción</th><th>Categoría</th><th>Crédito IVA</th><th className="num">Subtotal</th><th className="num">IVA</th><th className="num">Total</th><th>Estado</th><th /></tr></thead>
            <tbody>{items.map((e) => <tr key={e.id}><td className="muted">{fmtDate(e.date)}</td><td style={{ fontWeight: 600 }}>{e.description}{e.attachment_url && <a href={e.attachment_url} target="_blank" rel="noopener" className="badge badge--info" style={{ marginLeft: 6 }} title="Ver adjunto">Adjunto</a>}{e.reference && <div className="meta">{e.reference}</div>}</td><td className="muted">{e.category || "—"}</td><td className="muted">{e.iva_credit}</td><td className="num money">{fmtMoney(e.subtotal, e.currency)}</td><td className="num money">{fmtMoney(e.tax_amount, e.currency)}</td><td className="num money" style={{ fontWeight: 700 }}>{fmtMoney(e.total, e.currency)}</td><td><Badge status={e.status === "registrado" ? "creado" : e.status === "pagado" ? "pagada" : "anulada"} /></td><td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setEdit({ id: e.id, category_id: e.category_id ? String(e.category_id) : "", supplier_id: e.supplier_id ? String(e.supplier_id) : "", attachment_url: e.attachment_url || "", description: e.description, date: e.date, currency: e.currency, subtotal: String(Number(e.subtotal)), tax_rate: String(Number(e.tax_rate)), iva_credit: e.iva_credit, bank_account_id: e.bank_account_id ? String(e.bank_account_id) : "", reference: e.reference || "", status: e.status })}>Ver</button></td></tr>)}</tbody></table>
        )}
      </Card>

      <Card title="Proveedores" flush className="" extra={<button className="btn btn--ghost btn--sm" onClick={() => setSup({ name: "", tax_id: "", email: "", phone: "" })}><Icon d={I.plus} />Proveedor</button>}>
        {sups.length === 0 ? <Empty title="Sin proveedores" hint="Se crean al recibir XML o aquí; sirven para el D-151 y el control de compras." /> : (
          <table className="table"><thead><tr><th>Nombre</th><th>Cédula</th><th>Correo</th><th>Teléfono</th><th /></tr></thead>
            <tbody>{sups.map((x) => <tr key={x.id}><td style={{ fontWeight: 600 }}>{x.name}</td><td className="mono muted">{x.tax_id || "—"}</td><td className="muted">{x.email || "—"}</td><td className="muted">{x.phone || "—"}</td><td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setSup({ id: x.id, name: x.name, tax_id: x.tax_id || "", email: x.email || "", phone: x.phone || "" })}>Editar</button></td></tr>)}</tbody></table>
        )}
      </Card>

      {sup && (
        <Modal title={sup.id ? "Proveedor" : "Nuevo proveedor"} onClose={() => setSup(null)} foot={<><button className="btn btn--ghost" onClick={() => setSup(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveSup}>Guardar</button></>}>
          <Field label="Nombre o razón social"><input className="input" autoFocus value={sup.name} onChange={(e) => setSup({ ...sup, name: e.target.value })} /></Field>
          <div className="grid-3">
            <Field label="Cédula"><input className="input input--mono" style={{ textAlign: "left" }} value={sup.tax_id || ""} onChange={(e) => setSup({ ...sup, tax_id: e.target.value })} /></Field>
            <Field label="Correo"><input className="input" value={sup.email || ""} onChange={(e) => setSup({ ...sup, email: e.target.value })} /></Field>
            <Field label="Teléfono"><input className="input" value={sup.phone || ""} onChange={(e) => setSup({ ...sup, phone: e.target.value })} /></Field>
          </div>
        </Modal>
      )}

      {edit && (
        <Modal title={edit.id ? "Gasto" : "Registrar gasto"} onClose={() => setEdit(null)} foot={<><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Guardar</button></>}>
          <Field label="Descripción"><input className="input" value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
          <div className="grid-3">
            <Field label="Fecha"><input className="input" type="date" value={edit.date} onChange={(e) => setEdit({ ...edit, date: e.target.value })} /></Field>
            <Field label="Categoría"><select className="select" value={edit.category_id} onChange={(e) => setEdit({ ...edit, category_id: e.target.value })}><option value="">—</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
            <Field label="Cuenta"><select className="select" value={edit.bank_account_id} onChange={(e) => setEdit({ ...edit, bank_account_id: e.target.value })}><option value="">—</option>{banks.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>
            <Field label="Subtotal (sin IVA)"><input className="input input--mono" value={edit.subtotal} onChange={(e) => setEdit({ ...edit, subtotal: e.target.value })} /></Field>
            <Field label="IVA %"><select className="select" value={edit.tax_rate} onChange={(e) => setEdit({ ...edit, tax_rate: e.target.value })}>{["13", "4", "2", "1", "0"].map((r) => <option key={r} value={r}>{r}%</option>)}</select></Field>
            <Field label="Condición del IVA"><select className="select" value={edit.iva_credit} onChange={(e) => setEdit({ ...edit, iva_credit: e.target.value })}><option value="credito">Genera crédito</option><option value="no_credito">No genera crédito</option><option value="proporcional">Proporcional (prorrata)</option></select></Field>
            <Field label="Referencia" hint="Factura del proveedor, clave…"><input className="input" value={edit.reference} onChange={(e) => setEdit({ ...edit, reference: e.target.value })} /></Field>
            <Field label="Estado"><select className="select" value={edit.status} onChange={(e) => setEdit({ ...edit, status: e.target.value })}><option value="registrado">Registrado</option><option value="pagado">Pagado</option><option value="anulado">Anulado</option></select></Field>
            <Field label="Total"><div className="input money" style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", background: "var(--bg-2)", fontWeight: 700 }}>{fmtMoney(subtotal + iva, edit.currency)}</div></Field>
            <Field label="Proveedor"><select className="select" value={edit.supplier_id} onChange={(e) => setEdit({ ...edit, supplier_id: e.target.value })}><option value="">—</option>{sups.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
          </div>
          <Field label="Comprobante adjunto" hint="Foto o PDF de la factura del proveedor.">
            {edit.attachment_url
              ? <div style={{ display: "flex", gap: 8, alignItems: "center" }}><a className="btn btn--soft btn--sm" href={edit.attachment_url} target="_blank" rel="noopener">Ver adjunto</a><button className="btn btn--ghost btn--sm" onClick={() => setEdit({ ...edit, attachment_url: "" })}>Quitar</button></div>
              : <Dropzone compact accept="image/*,application/pdf" label="Adjuntar foto o PDF" onDone={(m) => setEdit({ ...edit, attachment_url: m.url })} />}
          </Field>
        </Modal>
      )}
    </>
  );
}
