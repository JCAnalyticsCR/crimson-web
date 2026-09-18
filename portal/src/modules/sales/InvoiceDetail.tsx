/* Detalle de factura: editor + bloque de pagos + modal de pago + enlace de pago (WhatsApp / copiar / abrir). */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, fmtDate, fmtMoney, type Invoice } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Field, I, Icon, Modal } from "../../ui/components";
import DocEditor from "./DocEditor";

const METHODS = [["efectivo", "Efectivo"], ["sinpe", "SINPE Móvil"], ["transferencia", "Transferencia"], ["tarjeta", "Tarjeta"], ["onvo", "ONVO (en línea)"], ["paypal", "PayPal"]];
const KINDS = [["captura", "Captura (recibido)"], ["autorizacion", "Autorización"], ["devolucion", "Devolución"], ["reembolso", "Reembolso"], ["reautorizacion", "Re-autorización"]];

export default function InvoiceDetail() {
  const { id } = useParams();
  const { toast } = useSession();
  const [inv, setInv] = useState<Invoice | null>(null);
  const [pay, setPay] = useState(false);
  const [link, setLink] = useState<{ url: string; whatsapp_url: string; expires_at: string; opened_count: number } | null>(null);
  const [xmls, setXmls] = useState<{ id: number; doc_type: string; consecutive: string; clave: string | null; status: string; message: string | null; provider: string; has_document: boolean; has_response: boolean }[] | null>(null);
  const [nc, setNc] = useState<string | null>(null);
  const [mail, setMail] = useState<{ to: string; message: string } | null>(null);
  const emit = async () => { try { const r = await api<{ status: string; clave: string; message: string }>(`/invoices/${id}/emit`, { method: "POST" }); toast(`Hacienda: ${r.status} · ${r.message || ""}`); reload(); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } };
  const loadXml = () => api(`/invoices/${id}/xml`).then((r) => setXmls(r as never)).catch((e) => toast(e.message, "bad"));
  const sendNc = async () => { if (!nc) return; try { await api(`/invoices/${id}/credit-note`, { method: "POST", json: { reason: nc } }); toast("Nota de crédito aceptada; factura anulada"); setNc(null); reload(); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } };
  const sendMail = async () => { if (!mail) return; try { const r = await api<{ status: string; note: string | null }>(`/invoices/${id}/email`, { method: "POST", json: { to: mail.to || null, message: mail.message || null } }); toast(r.note || `Correo ${r.status}`); setMail(null); reload(); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } };
  const [form, setForm] = useState({ method: "sinpe", kind: "captura", amount: "", external_ref: "", paid_at: new Date().toISOString().slice(0, 10), notify_customer: true });
  const reload = () => api<Invoice>(`/invoices/${id}`).then(setInv);
  useEffect(() => { reload(); }, [id]);
  useEffect(() => { if (inv) setForm((f) => ({ ...f, amount: f.amount || inv.balance })); }, [inv]);

  const submitPay = async () => {
    try { setInv(await api<Invoice>(`/invoices/${id}/payments`, { method: "POST", json: { ...form, amount: form.amount } })); setPay(false); toast("Pago registrado"); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const getLink = async () => { try { setLink(await api(`/invoices/${id}/payment-link`, { method: "POST" })); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } };

  return (
    <>
      <DocEditor kind="invoice" key={`${inv?.status}-${inv?.payments.length ?? 0}`} />
      {inv && (
        <div className="doc" style={{ marginTop: -4 }}>
          <Card title="Pagos" flush extra={<div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--ghost btn--sm" onClick={getLink}><Icon d={I.link} />Enlace de pago</button>
            <button className="btn btn--crimson btn--sm" disabled={inv.status === "anulada" || Number(inv.balance) <= 0} onClick={() => setPay(true)}><Icon d={I.plus} />Agregar pago</button>
          </div>}>
            {inv.payments.length === 0 ? <div className="empty" style={{ padding: 26 }}><span className="meta">Sin pagos</span><div className="h3">Saldo pendiente {fmtMoney(inv.balance, inv.currency)}</div><p className="muted">Registrá un pago manual o compartí el enlace de pago por WhatsApp.</p></div> : (
              <table className="table">
                <thead><tr><th>Fecha</th><th>Método</th><th>Tipo</th><th>Referencia</th><th className="num">Monto</th><th>Estado</th></tr></thead>
                <tbody>{inv.payments.map((p) => <tr key={p.id}><td className="muted">{fmtDate(p.paid_at)}</td><td style={{ textTransform: "capitalize" }}>{p.method}</td><td className="muted" style={{ textTransform: "capitalize" }}>{p.kind}</td><td className="mono muted">{p.external_ref || "—"}</td><td className="num money">{(p.kind === "devolucion" || p.kind === "reembolso") ? "−" : ""}{fmtMoney(p.amount, p.currency)}</td><td><Badge status={p.status} /></td></tr>)}</tbody>
              </table>
            )}
          </Card>
          <Card title="Documentos electrónicos" extra={<div style={{ display: "flex", gap: 8 }}><button className="btn btn--ghost btn--sm" onClick={loadXml}>XMLs</button>{inv.einvoice_status !== "aceptada" && inv.status !== "anulada" && <button className="btn btn--crimson btn--sm" onClick={emit}><Icon d={I.check} />Emitir a Hacienda</button>}{inv.einvoice_status === "aceptada" && inv.status !== "anulada" && <button className="btn btn--danger btn--sm" onClick={() => setNc("")}>Anular con NC</button>}</div>}>
            <div className="status-line"><Badge status={inv.einvoice_status === "sin_emitir" ? "pendiente" : inv.einvoice_status === "aceptada" ? "confirmado" : inv.einvoice_status === "rechazada" ? "fallido" : inv.einvoice_status} /><span>Hacienda · v4.4 · {inv.consecutive}</span></div>
            {inv.clave && <p className="mono" style={{ fontSize: 11, marginTop: 8, wordBreak: "break-all", color: "var(--text-2)" }}>Clave {inv.clave}</p>}
            <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
              <a className="btn btn--soft btn--sm" href={`/api/invoices/${id}/pdf`} target="_blank" rel="noopener">PDF / Imprimir</a>
              <button className="btn btn--soft btn--sm" onClick={() => setMail({ to: "", message: "" })}>Enviar por correo</button>
            </div>
          </Card>
        </div>
      )}

      {pay && inv && (
        <Modal title="Agregar pago" onClose={() => setPay(false)} foot={<><button className="btn btn--ghost" onClick={() => setPay(false)}>Cancelar</button><button className="btn btn--crimson" onClick={submitPay}>Agregar pago</button></>}>
          <div className="grid-2">
            <Field label="Divisa"><input className="input" value={inv.currency} disabled /></Field>
            <Field label="Monto"><input className="input input--mono" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></Field>
            <Field label="Método"><select className="select" value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}>{METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
            <Field label="Tipo"><select className="select" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
            <Field label="Referencia externa" hint="Comprobante SINPE, número de transferencia…"><input className="input" value={form.external_ref} onChange={(e) => setForm({ ...form, external_ref: e.target.value })} /></Field>
            <Field label="Fecha"><input className="input" type="date" value={form.paid_at} onChange={(e) => setForm({ ...form, paid_at: e.target.value })} /></Field>
          </div>
          <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={form.notify_customer} onChange={(e) => setForm({ ...form, notify_customer: e.target.checked })} />Enviar confirmación de pago al cliente</label>
        </Modal>
      )}

      {xmls && (
        <Modal title="Documentos electrónicos (XML)" onClose={() => setXmls(null)} foot={<button className="btn btn--ghost" onClick={() => setXmls(null)}>Cerrar</button>}>
          {xmls.length === 0 ? <p className="muted">Aún no se ha emitido. Configurá el proveedor fiscal en Ajustes → Facturación y presioná “Emitir a Hacienda”.</p> : (
            <table className="table"><thead><tr><th>Tipo</th><th>Consecutivo</th><th>Estado</th><th>Documento</th><th>Respuesta</th></tr></thead>
              <tbody>{xmls.map((x) => <tr key={x.id}><td style={{ fontWeight: 600 }}>{x.doc_type}</td><td className="mono muted">{x.consecutive}</td><td><Badge status={x.status === "aceptada" ? "confirmado" : x.status === "rechazada" ? "fallido" : "pendiente"} />{x.message && <div className="meta" style={{ textTransform: "none" }}>{x.message}</div>}</td><td>{x.has_document && <a className="btn btn--ghost btn--sm" href={`/api/invoices/${id}/xml/${x.id}/document`}>Descargar</a>}</td><td>{x.has_response && <a className="btn btn--ghost btn--sm" href={`/api/invoices/${id}/xml/${x.id}/response`}>Descargar</a>}</td></tr>)}</tbody></table>
          )}
        </Modal>
      )}
      {nc !== null && (
        <Modal title="Anular con nota de crédito" onClose={() => setNc(null)} foot={<><button className="btn btn--ghost" onClick={() => setNc(null)}>Cancelar</button><button className="btn btn--danger" onClick={sendNc}>Emitir NC y anular</button></>}>
          <p className="muted" style={{ fontSize: 13 }}>La factura fue aceptada por Hacienda: se anula emitiendo una Nota de Crédito que referencia su clave. El inventario vendido se repone.</p>
          <Field label="Motivo"><input className="input" value={nc} onChange={(e) => setNc(e.target.value)} placeholder="Error en cantidad, devolución…" /></Field>
        </Modal>
      )}
      {mail && inv && (
        <Modal title="Enviar factura por correo" onClose={() => setMail(null)} foot={<><button className="btn btn--ghost" onClick={() => setMail(null)}>Cancelar</button><button className="btn btn--crimson" onClick={sendMail}>Enviar</button></>}>
          <Field label="Para" hint="Vacío = correo del cliente. Incluye PDF y enlace de pago si hay saldo."><input className="input" type="email" value={mail.to} onChange={(e) => setMail({ ...mail, to: e.target.value })} placeholder={inv.customer_name || ""} /></Field>
          <Field label="Mensaje"><textarea className="textarea" value={mail.message} onChange={(e) => setMail({ ...mail, message: e.target.value })} /></Field>
        </Modal>
      )}
      {link && inv && (
        <Modal title="Enlace de pago para la factura" onClose={() => setLink(null)} foot={<button className="btn btn--ghost" onClick={() => setLink(null)}>Cerrar</button>}>
          <p className="muted" style={{ fontSize: 13 }}>URL única de {inv.number}. Vence {fmtDate(link.expires_at.slice(0, 10))} · abierta {link.opened_count} veces.</p>
          <div className="search" style={{ maxWidth: "none" }}><Icon d={I.link} size={16} /><input readOnly value={link.url} onFocus={(e) => e.currentTarget.select()} /></div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <a className="btn" style={{ background: "#25D366" }} href={link.whatsapp_url} target="_blank" rel="noopener"><Icon d={I.whatsapp} />Compartir por WhatsApp</a>
            <button className="btn btn--soft" onClick={() => { navigator.clipboard.writeText(link.url); toast("Enlace copiado"); }}><Icon d={I.copy} />Copiar</button>
            <a className="btn btn--ghost" href={link.url} target="_blank" rel="noopener"><Icon d={I.arrow} />Ir al enlace</a>
          </div>
        </Modal>
      )}
    </>
  );
}
