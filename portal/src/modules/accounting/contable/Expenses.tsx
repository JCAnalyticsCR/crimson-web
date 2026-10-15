/* Gastos: filtros, totales del filtro en colones y el modal con divisa, tipo de cambio, condicion del IVA, pago,
   cuenta -> chequera o tarjeta y adjunto. Un gasto que viene de un comprobante electronico queda con los montos en
   solo lectura (salen del XML) y muestra sus archivos. */
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, downloadFile, fmtDate, fmtMoney, openFile } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Badge, Card, Empty, Field, I, Icon, Loading, Modal } from "../../../ui/components";
import AuthLink from "../../../ui/AuthLink";
import { Dropzone } from "../../../ui/MediaPicker";
import { VerArchivo } from "../../../ui/VerArchivo";
import { IVA_CONDITIONS } from "../hacienda";

type Named = { id: number; name: string };
type Instrument = { id: number; kind: "chequera" | "tarjeta_debito"; name: string; last4: string | null; next_check: number | null; active: boolean };
type Account = { id: number; name: string; currency: string; instruments: Instrument[] };
export type Exp = {
  id: number; category_id: number | null; category: string | null; supplier_id: number | null; supplier_name: string | null; description: string; date: string; currency: string; fx_rate: string;
  subtotal: string; tax_rate: string; tax_amount: string; total: string; total_crc: string; iva_credit: string; tax_credit_amount: string | null; bank_account_id: number | null;
  reference: string | null; status: string; attachment_url: string | null; notes: string | null; payment_method: string | null; purchase_type: string; due_date: string | null;
  origin: string; received_document_id: number | null; recurrence_id: number | null; bank_instrument_id: number | null; check_number: string | null; locked: boolean;
};
type Sum = { count: number; total_crc: string; tax_crc: string; credit_crc: string };
type Res = { rows: (string | number)[][]; totals: { resultado: string } };
const ORIGIN: Record<string, string> = { xml: "XML", recurrente: "Recurrente", banco: "Banco", planilla: "Planilla", manual: "Manual" };
const PAY = [["", "—"], ["efectivo", "Efectivo"], ["tarjeta", "Tarjeta"], ["transferencia", "Transferencia"], ["sinpe", "SINPE"], ["cheque", "Cheque"], ["otro", "Otro"]];
const blank = { category_id: "", supplier_id: "", attachment_url: "", description: "", date: new Date().toISOString().slice(0, 10), currency: "CRC", fx_rate: "", subtotal: "", tax_rate: "13", iva_credit: "credito", tax_credit_amount: "", bank_account_id: "", bank_instrument_id: "", check_number: "", payment_method: "", purchase_type: "contado", due_date: "", notes: "", reference: "", status: "registrado" };
type Edit = typeof blank & { id?: number; locked?: boolean; received_document_id?: number | null };
const month = () => new Date().toISOString().slice(0, 7);

export default function Expenses() {
  const { toast, allows } = useSession();
  const [params] = useSearchParams();
  const [rows, setRows] = useState<Exp[] | null>(null);
  const [sum, setSum] = useState<Sum | null>(null);
  const [res, setRes] = useState<Res | null>(null);
  const [cats, setCats] = useState<Named[]>([]);
  const [sups, setSups] = useState<Named[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [f, setF] = useState({ month: month(), category_id: "", supplier_id: "", origin: "", currency: "", status: "", q: "" });
  const [edit, setEdit] = useState<Edit | null>(null);

  const qs = () => {
    const p = new URLSearchParams({ limit: "500" });
    if (f.month) {
      const [y, m] = f.month.split("-").map(Number);
      p.set("from", `${f.month}-01`); p.set("to", new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10));
    }
    for (const k of ["category_id", "supplier_id", "origin", "currency", "status", "q"] as const) if (f[k]) p.set(k, f[k]);
    return p.toString();
  };
  const load = () => {
    api<Exp[]>(`/expenses?${qs()}`).then(setRows).catch((e) => toast(e.message, "bad"));
    api<Sum>(`/expenses/summary?${qs()}`).then(setSum).catch(() => setSum(null));
  };
  useEffect(load, [f]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api<Res>("/reports/resultados").then(setRes).catch(() => setRes(null));
    api<Named[]>("/expense-categories?active=1").then(setCats).catch(() => setCats([]));
    api<Named[]>("/suppliers").then(setSups).catch(() => setSups([]));
    api<Account[]>("/banking/accounts").then(setAccounts).catch(() => setAccounts([])); // contabilidad no tiene /settings
  }, []);
  // ?expense=ID (desde una recepcion: "Ver gasto") abre ese gasto
  useEffect(() => {
    const id = Number(params.get("expense"));
    if (!id) return;
    api<Exp[]>(`/expenses?limit=500`).then((all) => { const e = all.find((x) => x.id === id); if (e) open(e); }).catch(() => undefined);
  }, [params]); // eslint-disable-line react-hooks/exhaustive-deps

  const open = (e: Exp) => setEdit({
    id: e.id, locked: e.locked, received_document_id: e.received_document_id, category_id: String(e.category_id ?? ""), supplier_id: String(e.supplier_id ?? ""), attachment_url: e.attachment_url || "",
    description: e.description, date: e.date, currency: e.currency, fx_rate: String(Number(e.fx_rate)), subtotal: String(Number(e.subtotal)), tax_rate: String(Number(e.tax_rate)), iva_credit: e.iva_credit,
    tax_credit_amount: e.tax_credit_amount === null ? "" : String(Number(e.tax_credit_amount)), bank_account_id: String(e.bank_account_id ?? ""), bank_instrument_id: String(e.bank_instrument_id ?? ""),
    check_number: e.check_number || "", payment_method: e.payment_method || "", purchase_type: e.purchase_type || "contado", due_date: e.due_date || "", notes: e.notes || "", reference: e.reference || "", status: e.status,
  });
  const save = async () => {
    if (!edit) return;
    const { id, locked, received_document_id, ...b } = edit;
    void received_document_id;
    const body = {
      ...b,
      category_id: b.category_id ? Number(b.category_id) : null, supplier_id: b.supplier_id ? Number(b.supplier_id) : null, bank_account_id: b.bank_account_id ? Number(b.bank_account_id) : null,
      bank_instrument_id: b.bank_instrument_id ? Number(b.bank_instrument_id) : null, attachment_url: b.attachment_url || null, subtotal: Number(b.subtotal || 0), tax_rate: Number(b.tax_rate),
      fx_rate: b.currency === "CRC" || b.fx_rate === "" ? null : Number(b.fx_rate), tax_credit_amount: b.iva_credit === "credito_parcial" && b.tax_credit_amount !== "" ? Number(b.tax_credit_amount) : locked ? null : null,
      payment_method: b.payment_method || null, due_date: b.purchase_type === "credito" && b.due_date ? b.due_date : null, check_number: b.check_number || null, notes: b.notes || null, reference: b.reference || null,
    };
    try {
      const r = await api<Exp & { warnings?: string[] }>(id ? `/expenses/${id}` : "/expenses", { method: id ? "PUT" : "POST", json: body });
      toast(r.warnings?.length ? r.warnings[0] : "Gasto guardado"); setEdit(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const acct = accounts.find((a) => String(a.id) === edit?.bank_account_id);
  const ins = acct?.instruments.find((i) => String(i.id) === edit?.bank_instrument_id);
  const sub = Number(edit?.subtotal || 0), iva = sub * Number(edit?.tax_rate || 0) / 100;
  const ro = !!edit?.locked;

  return (
    <>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
        <AuthLink path="/reports/gastos?format=xlsx" download="gastos.xlsx"><Icon d={I.reports} />Excel</AuthLink>
        {allows("accounting.crear") && <button className="btn btn--crimson btn--sm" onClick={() => setEdit({ ...blank })}><Icon d={I.plus} />Registrar gasto</button>}
      </div>
      <div className="kpi">
        {res && <div className="kpi__card"><span className="kpi__corner" /><div className="kpi__label">Resultado del mes (sin IVA)</div><div className="kpi__value">{fmtMoney(res.totals.resultado)}</div><div className="kpi__sub">{res.rows.map((r) => <span key={String(r[0])}>{r[0]} <b className="money">{fmtMoney(r[1])}</b></span>)}</div></div>}
        {sum && <div className="kpi__card kpi__card--light"><span className="kpi__corner" /><div className="kpi__label">Gastos del filtro (en colones)</div><div className="kpi__value">{fmtMoney(sum.total_crc)}</div><div className="kpi__sub"><span>{sum.count} gastos</span><span>IVA <b className="money">{fmtMoney(sum.tax_crc)}</b></span><span>IVA acreditable <b className="money">{fmtMoney(sum.credit_crc)}</b></span></div></div>}
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input className="input" type="month" style={{ maxWidth: 160 }} value={f.month} onChange={(e) => setF({ ...f, month: e.target.value })} />
        <select className="select" style={{ maxWidth: 180 }} value={f.category_id} onChange={(e) => setF({ ...f, category_id: e.target.value })}><option value="">Todas las categorías</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
        <select className="select" style={{ maxWidth: 180 }} value={f.supplier_id} onChange={(e) => setF({ ...f, supplier_id: e.target.value })}><option value="">Todos los proveedores</option>{sups.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
        <select className="select" style={{ maxWidth: 150 }} value={f.origin} onChange={(e) => setF({ ...f, origin: e.target.value })}><option value="">Todo origen</option>{Object.entries(ORIGIN).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <select className="select" style={{ maxWidth: 110 }} value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })}><option value="">Divisa</option><option>CRC</option><option>USD</option></select>
        <select className="select" style={{ maxWidth: 140 }} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">Todo estado</option><option value="registrado">Registrado</option><option value="pagado">Pagado</option><option value="anulado">Anulado</option></select>
        <input className="input" style={{ maxWidth: 220 }} placeholder="Buscar" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} />
      </div>
      <Card flush>
        {rows === null ? <Loading /> : rows.length === 0 ? <Empty hint="Registrá compras y gastos; los que vienen de una factura electrónica aceptada se crean solos." /> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table"><thead><tr><th>Fecha</th><th>Proveedor</th><th>Descripción</th><th>Categoría</th><th>Origen</th><th>Divisa</th><th className="num">Subtotal</th><th className="num">IVA</th><th className="num">IVA acreditable</th><th className="num">Total</th><th className="num">Total ₡</th><th>Estado</th><th /></tr></thead>
              <tbody>{rows.map((e) => <tr key={e.id}>
                <td className="muted">{fmtDate(e.date)}</td>
                <td>{e.supplier_name || "—"}</td>
                <td style={{ fontWeight: 600 }}>{e.description}{e.attachment_url && <VerArchivo url={e.attachment_url} caption={e.description} className="badge badge--info badge--btn">Adjunto</VerArchivo>}</td>
                <td className="muted">{e.category || "—"}</td>
                <td><span className={`badge ${e.origin === "xml" ? "badge--ok" : "badge--muted"}`}>{ORIGIN[e.origin] || e.origin}</span></td>
                <td className="muted">{e.currency}</td>
                <td className="num money">{fmtMoney(e.subtotal, e.currency)}</td>
                <td className="num money">{fmtMoney(e.tax_amount, e.currency)}</td>
                <td className="num money">{e.tax_credit_amount === null ? "prorrata" : fmtMoney(e.tax_credit_amount, e.currency)}</td>
                <td className="num money" style={{ fontWeight: 700 }}>{fmtMoney(e.total, e.currency)}</td>
                <td className="num money">{fmtMoney(e.total_crc)}</td>
                <td><Badge status={e.status === "registrado" ? "creado" : e.status === "pagado" ? "pagada" : "anulada"} /></td>
                <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => open(e)}>Ver</button></td>
              </tr>)}</tbody></table>
          </div>
        )}
      </Card>

      {edit && (
        <Modal title={edit.id ? "Gasto" : "Registrar gasto"} wide onClose={() => setEdit(null)} foot={<><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button>{allows(edit.id ? "accounting.editar" : "accounting.crear") && <button className="btn btn--crimson" onClick={save}>Guardar</button>}</>}>
          {ro && (
            <div className="acc-warn">
              Viene de un comprobante electrónico: montos, divisa, fecha y condición salen del XML y no se editan.
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                <button className="btn btn--soft btn--sm" onClick={() => downloadFile(`/reception/${edit.received_document_id}/file/document`, "comprobante.xml").catch((e) => toast(e.message, "bad"))}>XML</button>
                <button className="btn btn--soft btn--sm" onClick={() => openFile(`/reception/${edit.received_document_id}/file/pdf`).catch((e) => toast(e.message, "bad"))}>PDF</button>
                <button className="btn btn--soft btn--sm" onClick={() => downloadFile(`/reception/${edit.received_document_id}/file/todo`, "comprobante.zip").catch((e) => toast(e.message, "bad"))}>Todo (.zip)</button>
                <Link className="btn btn--ghost btn--sm" to={`/contabilidad/facturas/${edit.received_document_id}`}>Ver recepción</Link>
              </div>
            </div>
          )}
          <Field label="Descripción"><input className="input" value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
          <div className="grid-3">
            <Field label="Fecha"><input className="input" type="date" disabled={ro} value={edit.date} onChange={(e) => setEdit({ ...edit, date: e.target.value })} /></Field>
            <Field label="Divisa"><select className="select" disabled={ro} value={edit.currency} onChange={(e) => setEdit({ ...edit, currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field>
            {edit.currency !== "CRC" && <Field label="Tipo de cambio" hint="Vacío = venta del BCCR de esa fecha."><input className="input input--mono" disabled={ro} value={edit.fx_rate} onChange={(e) => setEdit({ ...edit, fx_rate: e.target.value })} /></Field>}
            <Field label="Subtotal (sin IVA)"><input className="input input--mono" disabled={ro} value={edit.subtotal} onChange={(e) => setEdit({ ...edit, subtotal: e.target.value })} /></Field>
            <Field label="IVA %"><select className="select" disabled={ro} value={edit.tax_rate} onChange={(e) => setEdit({ ...edit, tax_rate: e.target.value })}>{(ro ? [edit.tax_rate] : ["13", "4", "2", "1", "0"]).map((r) => <option key={r} value={r}>{r}%</option>)}</select></Field>
            <Field label="Total"><div className="input money" style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", background: "var(--bg-2)", fontWeight: 700 }}>{fmtMoney(ro ? Number(edit.subtotal) * (1 + Number(edit.tax_rate) / 100) : sub + iva, edit.currency)}</div></Field>
            <Field label="Condición del IVA"><select className="select" disabled={ro} value={edit.iva_credit} onChange={(e) => setEdit({ ...edit, iva_credit: e.target.value })}>{IVA_CONDITIONS.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select></Field>
            {edit.iva_credit === "credito_parcial" && <Field label="IVA acreditable"><input className="input input--mono" disabled={ro} value={edit.tax_credit_amount} onChange={(e) => setEdit({ ...edit, tax_credit_amount: e.target.value })} /></Field>}
            <Field label="Categoría"><select className="select" value={edit.category_id} onChange={(e) => setEdit({ ...edit, category_id: e.target.value })}><option value="">—</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
            <Field label="Proveedor"><select className="select" disabled={ro} value={edit.supplier_id} onChange={(e) => setEdit({ ...edit, supplier_id: e.target.value })}><option value="">—</option>{sups.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
            <Field label="Método de pago"><select className="select" value={edit.payment_method} onChange={(e) => setEdit({ ...edit, payment_method: e.target.value })}>{PAY.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
            <Field label="Compra"><select className="select" value={edit.purchase_type} onChange={(e) => setEdit({ ...edit, purchase_type: e.target.value })}><option value="contado">Contado</option><option value="credito">Crédito</option></select></Field>
            {edit.purchase_type === "credito" && <Field label="Vence"><input className="input" type="date" value={edit.due_date} onChange={(e) => setEdit({ ...edit, due_date: e.target.value })} /></Field>}
            <Field label="Cuenta"><select className="select" value={edit.bank_account_id} onChange={(e) => setEdit({ ...edit, bank_account_id: e.target.value, bank_instrument_id: "" })}><option value="">—</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name} · {a.currency}</option>)}</select></Field>
            {acct && acct.instruments.length > 0 && <Field label="Chequera o tarjeta"><select className="select" value={edit.bank_instrument_id} onChange={(e) => setEdit({ ...edit, bank_instrument_id: e.target.value })}><option value="">—</option>{acct.instruments.filter((i) => i.active).map((i) => <option key={i.id} value={i.id}>{i.kind === "chequera" ? `Chequera ${i.name}` : `Tarjeta ${i.name} ····${i.last4}`}</option>)}</select></Field>}
            {ins?.kind === "chequera" && <Field label="Número de cheque" hint={ins.next_check ? `Siguiente: ${ins.next_check}` : undefined}><input className="input input--mono" value={edit.check_number} onChange={(e) => setEdit({ ...edit, check_number: e.target.value })} /></Field>}
            <Field label="Estado"><select className="select" value={edit.status} onChange={(e) => setEdit({ ...edit, status: e.target.value })}><option value="registrado">Registrado</option><option value="pagado">Pagado</option><option value="anulado">Anulado</option></select></Field>
            {!ro && <Field label="Referencia" hint="Factura del proveedor, clave…"><input className="input" value={edit.reference} onChange={(e) => setEdit({ ...edit, reference: e.target.value })} /></Field>}
          </div>
          <Field label="Notas"><textarea className="textarea" value={edit.notes} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} /></Field>
          <Field label="Comprobante adjunto" hint="Foto o PDF de la factura del proveedor.">
            {edit.attachment_url
              ? <div style={{ display: "flex", gap: 8, alignItems: "center" }}><VerArchivo url={edit.attachment_url} caption={edit.description} className="btn btn--soft btn--sm">Ver adjunto</VerArchivo><button className="btn btn--ghost btn--sm" onClick={() => setEdit({ ...edit, attachment_url: "" })}>Quitar</button></div>
              : <Dropzone compact accept="image/*,application/pdf" label="Adjuntar foto o PDF" privado="accounting" onDone={(m) => setEdit({ ...edit, attachment_url: m.url })} />}
          </Field>
        </Modal>
      )}
    </>
  );
}
