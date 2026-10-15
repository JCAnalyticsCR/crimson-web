/* Detalle de factura: editor + bloque de pagos + modal de pago + enlace de pago (WhatsApp / copiar / abrir). */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, fmtDate, fmtMoney, type Invoice } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Field, I, Icon, Modal } from "../../ui/components";
import DocEditor from "./DocEditor";
import AuthLink from "../../ui/AuthLink";

const METHODS = [["efectivo", "Efectivo"], ["sinpe", "SINPE Móvil"], ["transferencia", "Transferencia"], ["tarjeta", "Tarjeta"], ["onvo", "ONVO (en línea)"], ["paypal", "PayPal"]];
const KINDS = [["captura", "Captura (recibido)"], ["autorizacion", "Autorización"], ["devolucion", "Devolución"], ["reembolso", "Reembolso"], ["reautorizacion", "Re-autorización"]];
type XmlDoc = {
  id: number; doc_type: string; consecutive: string; clave: string | null; status: string; message: string | null; provider: string; has_document: boolean; has_response: boolean;
  mode: string | null; last_error: string | null; emitted_at: string | null; delivered_at: string | null; hacienda_delivered_at: string | null; delivery_failed_at: string | null; hacienda_delivery_failed_at: string | null;
  receiver_message: string | null; receiver_detail: string | null; receiver_at: string | null; attempts: number;
};
const MODE_TAG: Record<string, string> = { simulado: "Simulado", pruebas: "Pruebas", produccion: "Producción" };
const EI_TEXT: Record<string, string> = { por_enviar: "Por enviar a Hacienda (se reintenta solo)", enviando: "Enviando a Hacienda", enviado: "Enviado: Hacienda lo está validando", procesando: "En proceso en Hacienda" };
const CLIENT_MSG: Record<string, string> = { "1": "Aceptada por el cliente", "2": "Aceptada parcialmente por el cliente", "3": "Rechazada por el cliente" };
const when = (s: string | null) => (s ? new Date(/([zZ]|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`).toLocaleString("es-CR", { dateStyle: "medium", timeStyle: "short" }) : "");

export default function InvoiceDetail() {
  const { id } = useParams();
  const { toast, allows } = useSession();
  const [inv, setInv] = useState<Invoice | null>(null);
  const [pay, setPay] = useState(false);
  const [link, setLink] = useState<{ url: string; whatsapp_url: string; expires_at: string; opened_count: number } | null>(null);
  const [xmls, setXmls] = useState<XmlDoc[] | null>(null);
  const [docs, setDocs] = useState<XmlDoc[]>([]);
  const [nc, setNc] = useState<string | null>(null);
  const [mail, setMail] = useState<{ to: string; message: string } | null>(null);
  const emit = async () => { try { const r = await api<{ status: string; clave: string; message: string }>(`/invoices/${id}/emit`, { method: "POST" }); toast(`Hacienda: ${r.status}${r.message ? ` · ${r.message}` : ""}`); reload(); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } };
  const refreshEi = async () => { try { const r = await api<{ status: string; doc_status: string; message: string | null; last_error: string | null }>(`/invoices/${id}/einvoice/refresh`, { method: "POST" }); toast(`Hacienda: ${EI_TEXT[r.doc_status] || r.doc_status}${r.last_error ? ` · ${r.last_error}` : ""}`); reload(); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } };
  const loadXml = () => api(`/invoices/${id}/xml`).then((r) => setXmls(r as never)).catch((e) => toast(e.message, "bad"));
  const sendNc = async () => { if (!nc) return; try { const r = await api<{ status: string }>(`/invoices/${id}/credit-note`, { method: "POST", json: { reason: nc } }); toast(r.status === "aceptada" ? "Nota de crédito aceptada; factura anulada" : "Nota de crédito enviada a Hacienda: la factura se anula cuando Hacienda la acepte"); setNc(null); reload(); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } };
  const sendMail = async () => { if (!mail) return; try { const r = await api<{ status: string; sent: boolean; note: string | null }>(`/invoices/${id}/email`, { method: "POST", json: { to: mail.to || null, message: mail.message || null } }); toast(r.note || `Correo ${r.status}`, r.sent ? undefined : "bad"); setMail(null); reload(); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } };
  const [form, setForm] = useState({ method: "sinpe", kind: "captura", amount: "", external_ref: "", paid_at: new Date().toISOString().slice(0, 10), notify_customer: true });
  const reload = () => { api<Invoice>(`/invoices/${id}`).then(setInv); api<XmlDoc[]>(`/invoices/${id}/xml`).then(setDocs).catch(() => setDocs([])); };
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
            {allows("payments.crear") && <button className="btn btn--crimson btn--sm" disabled={inv.status === "anulada" || Number(inv.balance) <= 0} onClick={() => setPay(true)}><Icon d={I.plus} />Agregar pago</button>}
          </div>}>
            {inv.payments.length === 0 ? <div className="empty" style={{ padding: 26 }}><span className="meta">Sin pagos</span><div className="h3">Saldo pendiente {fmtMoney(inv.balance, inv.currency)}</div><p className="muted">Registrá un pago manual o compartí el enlace de pago por WhatsApp.</p></div> : (
              <table className="table">
                <thead><tr><th>Fecha</th><th>Método</th><th>Tipo</th><th>Referencia</th><th className="num">Monto</th><th>Estado</th></tr></thead>
                <tbody>{inv.payments.map((p) => <tr key={p.id}><td className="muted">{fmtDate(p.paid_at)}</td><td style={{ textTransform: "capitalize" }}>{p.method}</td><td className="muted" style={{ textTransform: "capitalize" }}>{p.kind}</td><td className="mono muted">{p.external_ref || "—"}</td><td className="num money">{(p.kind === "devolucion" || p.kind === "reembolso") ? "−" : ""}{fmtMoney(p.amount, p.currency)}</td><td><Badge status={p.status} /></td></tr>)}</tbody>
              </table>
            )}
          </Card>
          <Card title="Documentos electrónicos" extra={<div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}><button className="btn btn--ghost btn--sm" onClick={loadXml}>XMLs</button>{allows("sales.enviar") && docs.some((x) => x.mode === "pruebas" || x.mode === "produccion") && <button className="btn btn--soft btn--sm" onClick={refreshEi}>Consultar estado</button>}{allows("sales.enviar") && inv.einvoice_status !== "aceptada" && inv.einvoice_status !== "pendiente" && inv.status !== "anulada" && <button className="btn btn--crimson btn--sm" onClick={emit}><Icon d={I.check} />Emitir a Hacienda</button>}{allows("sales.anular") && inv.einvoice_status === "aceptada" && inv.status !== "anulada" && <button className="btn btn--danger btn--sm" onClick={() => setNc("")}>Anular con NC</button>}</div>}>
            {(() => {
              const last = docs.filter((x) => x.doc_type === inv.doc_type).slice(-1)[0];
              const nc = docs.filter((x) => x.doc_type === "NC").slice(-1)[0];
              const client = docs.find((x) => x.receiver_message);
              return <>
                <div className="status-line"><Badge status={inv.einvoice_status === "sin_emitir" ? "pendiente" : inv.einvoice_status === "aceptada" ? "confirmado" : inv.einvoice_status === "rechazada" ? "fallido" : inv.einvoice_status} /><span>Hacienda · v4.4 · {inv.consecutive || "sin consecutivo (se asigna al emitir)"}</span>{last?.mode && <span className={`badge ${last.mode === "produccion" ? "badge--ok" : "badge--warn"}`}>{MODE_TAG[last.mode] || last.mode}</span>}</div>
                {last && EI_TEXT[last.status] && <p style={{ fontSize: 13, margin: "8px 0 0" }}>{EI_TEXT[last.status]}</p>}
                {last?.last_error && <p style={{ fontSize: 13, margin: "6px 0 0", color: "var(--bad)" }}>{last.last_error}</p>}
                {nc && nc.status !== "aceptada" && <p style={{ fontSize: 13, margin: "6px 0 0" }}>Nota de crédito {nc.consecutive}: {EI_TEXT[nc.status] || nc.status}{nc.last_error ? ` · ${nc.last_error}` : ""}</p>}
                {last && (last.mode === "pruebas" || last.mode === "produccion") && (
                  <p className="muted" style={{ fontSize: 12.5, margin: "6px 0 0" }}>Entregado al cliente: {last.delivered_at ? `XML y PDF el ${when(last.delivered_at)}` : last.delivery_failed_at ? "no se pudo (el correo falló varias veces: revise el correo del cliente y use Enviar por correo)" : "pendiente"}{last.hacienda_delivered_at ? ` · respuesta de Hacienda el ${when(last.hacienda_delivered_at)}` : last.hacienda_delivery_failed_at ? " · la respuesta de Hacienda no se pudo entregar" : ""}{last.mode === "pruebas" ? " (en pruebas va a los administradores, no al cliente)" : ""}</p>
                )}
                {client && (
                  <div className={client.receiver_message === "1" ? "" : "acc-warn"} style={{ marginTop: 8, fontSize: 13, padding: client.receiver_message === "1" ? 0 : undefined, borderRadius: 10, border: client.receiver_message === "1" ? undefined : "1px solid var(--warn)", background: client.receiver_message === "1" ? undefined : "var(--warn-soft)" }}>
                    <b>{CLIENT_MSG[client.receiver_message!]}</b>{client.receiver_detail ? ` · ${client.receiver_detail}` : ""}{client.receiver_at ? ` · ${when(client.receiver_at)}` : ""}
                    <div className="muted" style={{ fontSize: 12 }}>Según el archivo recibido por correo (firma no verificada).</div>
                    {client.receiver_message !== "1" && allows("sales.anular") && inv.einvoice_status === "aceptada" && inv.status !== "anulada" && <div style={{ marginTop: 6 }}><button className="btn btn--danger btn--sm" onClick={() => setNc("")}>Emitir nota de crédito</button></div>}
                  </div>
                )}
              </>;
            })()}
            {inv.clave && <p className="mono" style={{ fontSize: 11, marginTop: 8, wordBreak: "break-all", color: "var(--text-2)" }}>Clave {inv.clave}</p>}
            <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
              <AuthLink className="btn btn--soft btn--sm" path={`/invoices/${id}/pdf`}>PDF / Imprimir</AuthLink>
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
          {xmls.length === 0 ? <p className="muted">Aún no se ha emitido. Configurá el modo en Ajustes → Hacienda y presioná “Emitir a Hacienda”.</p> : (
            <table className="table"><thead><tr><th>Tipo</th><th>Consecutivo</th><th>Estado</th><th>Documento</th><th>Respuesta</th></tr></thead>
              <tbody>{xmls.map((x) => <tr key={x.id}><td style={{ fontWeight: 600 }}>{x.doc_type}</td><td className="mono muted">{x.consecutive}</td><td><Badge status={x.status === "aceptada" ? "confirmado" : x.status === "rechazada" ? "fallido" : x.status === "error" ? "error" : "pendiente"} />{x.mode && <span className="badge badge--muted" style={{ marginLeft: 4 }}>{MODE_TAG[x.mode] || x.mode}</span>}{(x.last_error || x.message) && <div className="meta" style={{ textTransform: "none" }}>{x.last_error || x.message}</div>}</td><td>{x.has_document && <AuthLink path={`/invoices/${id}/xml/${x.id}/document`} download={`${x.doc_type}-${x.consecutive}.xml`}>Descargar</AuthLink>}</td><td>{x.has_response && <AuthLink path={`/invoices/${id}/xml/${x.id}/response`} download={`respuesta-${x.consecutive}.xml`}>Descargar</AuthLink>}</td></tr>)}</tbody></table>
          )}
        </Modal>
      )}
      {nc !== null && (
        <Modal title="Anular con nota de crédito" onClose={() => setNc(null)} foot={<><button className="btn btn--ghost" onClick={() => setNc(null)}>Cancelar</button><button className="btn btn--danger" onClick={sendNc}>Emitir NC y anular</button></>}>
          <p className="muted" style={{ fontSize: 13 }}>La factura fue aceptada por Hacienda: se anula emitiendo una Nota de Crédito que referencia su clave. Con emisión directa, la factura se anula y el inventario vuelve cuando Hacienda acepta la nota.</p>
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
