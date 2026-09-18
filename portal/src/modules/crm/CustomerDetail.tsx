/* Ficha del cliente: KPIs, linea de tiempo (cotizaciones, facturas, pagos, pedidos y notas), contactos y datos. */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Empty, Field, I, Icon, Modal } from "../../ui/components";

type Cust = { id: number; name: string; id_type: string; id_number: string | null; email: string | null; phone: string | null; whatsapp: string | null; currency: string; notes: string | null; address: Record<string, string> | null; active: boolean; created_at: string };
type Ev = { at: string; kind: string; title: string; amount?: string; currency?: string; status: string | null; to?: string; id?: number; author?: string | null };
type Overview = { customer: Cust; kpis: { billed: string; due: string; invoices: number; quotes: number; last_purchase: string | null; overdue: number }; timeline: Ev[] };
type Contact = { id?: number; name: string; role: string; email: string; phone: string; receives_invoices: boolean };

const KIND: Record<string, { label: string; color: string; icon: string }> = {
  cotizacion: { label: "Cotización", color: "var(--info)", icon: I.quote },
  factura: { label: "Factura", color: "var(--crimson)", icon: I.invoice },
  pago: { label: "Pago", color: "var(--ok)", icon: I.pay },
  orden: { label: "Pedido", color: "var(--warn)", icon: I.box },
  nota: { label: "Nota", color: "var(--text-3)", icon: I.quote },
};

export default function CustomerDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const { toast } = useSession();
  const [ov, setOv] = useState<Overview | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [note, setNote] = useState("");
  const [filter, setFilter] = useState("");
  const [contact, setContact] = useState<Contact | null>(null);
  const [edit, setEdit] = useState<Cust | null>(null);

  const load = useCallback(() => {
    api<Overview>(`/customers/${id}/overview`).then(setOv).catch((e) => toast(e.message, "bad"));
    api<Contact[]>(`/customers/${id}/contacts`).then((cs) => setContacts(cs.map((c) => ({ ...c, role: c.role || "", email: c.email || "", phone: c.phone || "" }))));
  }, [id, toast]);
  useEffect(() => { load(); }, [load]);

  if (!ov) return <div style={{ display: "grid", placeItems: "center", minHeight: 300 }}><span className="spinner" /></div>;
  const c = ov.customer;
  const events = ov.timeline.filter((e) => !filter || e.kind === filter);

  const addNote = async () => {
    if (!note.trim()) return;
    try { await api(`/customers/${id}/notes`, { method: "POST", json: { body: note.trim() } }); setNote(""); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const delNote = async (nid?: number) => { if (!nid || !confirm("¿Borrar la nota?")) return; await api(`/customers/${id}/notes/${nid}`, { method: "DELETE" }); load(); };
  const saveContact = async () => {
    if (!contact) return;
    const { id: kid, ...body } = contact;
    try {
      await api(kid ? `/customers/${id}/contacts/${kid}` : `/customers/${id}/contacts`, { method: kid ? "PUT" : "POST", json: { ...body, email: body.email || null, role: body.role || null, phone: body.phone || null } });
      setContact(null); load(); toast("Contacto guardado");
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const delContact = async (kid?: number) => { if (!kid || !confirm("¿Quitar el contacto?")) return; await api(`/customers/${id}/contacts/${kid}`, { method: "DELETE" }); load(); };
  const saveCustomer = async () => {
    if (!edit) return;
    try {
      await api(`/customers/${id}`, { method: "PUT", json: { id_type: edit.id_type, id_number: edit.id_number || null, name: edit.name, email: edit.email || null, phone: edit.phone || null, whatsapp: edit.whatsapp || null, currency: edit.currency, notes: edit.notes || null, address: edit.address } });
      setEdit(null); load(); toast("Cliente actualizado");
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const archive = async () => {
    if (!confirm(`¿Archivar a ${c.name}? Sus documentos se conservan; deja de aparecer en listas.`)) return;
    await api(`/customers/${id}`, { method: "DELETE" }); toast("Cliente archivado"); nav("/clientes");
  };
  const wa = (c.whatsapp || c.phone || "").replace(/\D/g, "");

  return (
    <>
      <div className="page-head">
        <div><div className="meta"><Link to="/clientes" className="row-link">04 · Clientes</Link> / ficha</div><h1 className="h1">{c.name}</h1>
          <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>{c.id_type} · <span className="mono">{c.id_number || "sin identificación"}</span>{c.email ? ` · ${c.email}` : ""}{!c.active && <> · <span className="badge badge--muted">Archivado</span></>}</div>
        </div>
        <div className="page-head__actions">
          {wa && <a className="btn btn--ghost btn--sm" href={`https://wa.me/${wa.length === 8 ? "506" + wa : wa}`} target="_blank" rel="noopener"><Icon d={I.whatsapp} />WhatsApp</a>}
          <button className="btn btn--ghost btn--sm" onClick={() => setEdit({ ...c })}>Editar datos</button>
          <button className="btn btn--soft btn--sm" onClick={() => nav(`/cotizaciones/nueva?cliente=${c.id}`)}><Icon d={I.quote} />Cotizar</button>
          <button className="btn btn--crimson btn--sm" onClick={() => nav(`/facturas/nueva?cliente=${c.id}`)}><Icon d={I.invoice} />Facturar</button>
        </div>
      </div>

      <div className="kpi" style={{ marginBottom: 18 }}>
        <div className="kpi__card"><div className="kpi__label">Facturado</div><div className="kpi__value money">{fmtMoney(ov.kpis.billed, c.currency)}</div><div className="kpi__sub">{ov.kpis.invoices} facturas</div></div>
        <div className="kpi__card"><div className="kpi__label">Por cobrar</div><div className="kpi__value money" style={{ color: Number(ov.kpis.due) > 0 ? "var(--crimson)" : undefined }}>{fmtMoney(ov.kpis.due, c.currency)}</div><div className="kpi__sub">{ov.kpis.overdue ? `${ov.kpis.overdue} vencidas` : "al día"}</div></div>
        <div className="kpi__card kpi__card--light"><div className="kpi__label">Cotizaciones</div><div className="kpi__value">{ov.kpis.quotes}</div><div className="kpi__sub">últimas 50</div></div>
        <div className="kpi__card kpi__card--light"><div className="kpi__label">Última compra</div><div className="kpi__value" style={{ fontSize: 22 }}>{ov.kpis.last_purchase ? fmtDate(ov.kpis.last_purchase) : "—"}</div><div className="kpi__sub">cliente desde {fmtDate(c.created_at.slice(0, 10))}</div></div>
      </div>

      <div className="grid-2" style={{ gridTemplateColumns: "minmax(0, 1.6fr) minmax(0, 1fr)", alignItems: "start" }}>
        <Card title="Línea de tiempo" extra={<div className="tabs">{[["", "Todo"], ["factura", "Facturas"], ["pago", "Pagos"], ["cotizacion", "Cotizaciones"], ["nota", "Notas"]].map(([k, l]) => <button key={k} className={filter === k ? "is-active" : ""} onClick={() => setFilter(k)}>{l}</button>)}</div>}>
          <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
            <textarea className="textarea" style={{ minHeight: 44 }} placeholder="Agregar una nota interna (llamada, acuerdo, visita…)" value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) addNote(); }} />
            <button className="btn btn--crimson btn--sm" onClick={addNote} disabled={!note.trim()}>Anotar</button>
          </div>
          {events.length === 0 ? <Empty title="Sin movimientos" hint="Las cotizaciones, facturas, pagos y notas de este cliente aparecen aquí." /> : (
            <ol style={{ listStyle: "none", margin: 0, padding: 0, position: "relative" }}>
              <span style={{ position: "absolute", left: 15, top: 6, bottom: 6, width: 2, background: "var(--hair)" }} />
              {events.map((e, i) => {
                const k = KIND[e.kind] ?? KIND.nota;
                return (
                  <li key={i} style={{ display: "grid", gridTemplateColumns: "32px 1fr auto", gap: 12, padding: "10px 0", position: "relative" }}>
                    <span style={{ width: 32, height: 32, borderRadius: 999, background: "var(--surface)", border: `2px solid ${k.color}`, display: "grid", placeItems: "center", color: k.color, zIndex: 1 }}><Icon d={k.icon} size={14} /></span>
                    <div style={{ minWidth: 0 }}>
                      <div className="meta">{k.label} · {fmtDate(e.at.slice(0, 10))}{e.author ? ` · ${e.author}` : ""}</div>
                      {e.to ? <Link to={e.to} className="row-link" style={{ fontWeight: 600 }}>{e.title}</Link> : <div style={{ fontSize: 14, whiteSpace: "pre-wrap" }}>{e.title}</div>}
                    </div>
                    <div style={{ textAlign: "right", display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
                      {e.amount != null && <span className="money" style={{ fontWeight: 600 }}>{fmtMoney(e.amount, e.currency)}</span>}
                      {e.status && <Badge status={e.status} />}
                      {e.kind === "nota" && <button className="btn btn--ghost btn--sm" onClick={() => delNote(e.id)} title="Borrar nota"><Icon d={I.x} size={12} /></button>}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </Card>

        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <Card title="Contactos" flush extra={<button className="btn btn--ghost btn--sm" onClick={() => setContact({ name: "", role: "", email: "", phone: "", receives_invoices: false })}><Icon d={I.plus} />Agregar</button>}>
            {contacts.length === 0 ? <Empty title="Sin contactos" hint="Personas de la empresa: compras, contabilidad, junta…" /> : (
              <table className="table"><tbody>{contacts.map((k) => (
                <tr key={k.id}>
                  <td><b>{k.name}</b>{k.receives_invoices && <span className="badge badge--info" style={{ marginLeft: 6 }}>Facturas</span>}<div className="meta" style={{ textTransform: "none" }}>{[k.role, k.email, k.phone].filter(Boolean).join(" · ")}</div></td>
                  <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setContact(k)}>Editar</button><button className="btn btn--ghost btn--sm" onClick={() => delContact(k.id)}><Icon d={I.x} size={12} /></button></td>
                </tr>
              ))}</tbody></table>
            )}
          </Card>
          <Card title="Datos">
            <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "8px 14px", margin: 0, fontSize: 13 }}>
              <dt className="muted">Correo</dt><dd style={{ margin: 0 }}>{c.email || "—"}</dd>
              <dt className="muted">Teléfono</dt><dd style={{ margin: 0 }}>{c.phone || "—"}</dd>
              <dt className="muted">WhatsApp</dt><dd style={{ margin: 0 }}>{c.whatsapp || "—"}</dd>
              <dt className="muted">Divisa</dt><dd style={{ margin: 0 }}>{c.currency}</dd>
              <dt className="muted">Dirección</dt><dd style={{ margin: 0 }}>{c.address?.senas || "—"}</dd>
              <dt className="muted">Notas</dt><dd style={{ margin: 0, whiteSpace: "pre-wrap" }}>{c.notes || "—"}</dd>
            </dl>
            {c.active && <button className="btn btn--danger btn--sm" style={{ marginTop: 16 }} onClick={archive}>Archivar cliente</button>}
          </Card>
        </div>
      </div>

      {contact && (
        <Modal title={contact.id ? "Contacto" : "Nuevo contacto"} onClose={() => setContact(null)} foot={<><button className="btn btn--ghost" onClick={() => setContact(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveContact}>Guardar</button></>}>
          <div className="grid-2">
            <Field label="Nombre"><input className="input" autoFocus value={contact.name} onChange={(e) => setContact({ ...contact, name: e.target.value })} /></Field>
            <Field label="Cargo"><input className="input" value={contact.role} onChange={(e) => setContact({ ...contact, role: e.target.value })} /></Field>
            <Field label="Correo"><input className="input" type="email" value={contact.email} onChange={(e) => setContact({ ...contact, email: e.target.value })} /></Field>
            <Field label="Teléfono"><input className="input" value={contact.phone} onChange={(e) => setContact({ ...contact, phone: e.target.value })} /></Field>
          </div>
          <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={contact.receives_invoices} onChange={(e) => setContact({ ...contact, receives_invoices: e.target.checked })} />Recibe facturas y estados de cuenta</label>
        </Modal>
      )}
      {edit && (
        <Modal title="Datos del cliente" onClose={() => setEdit(null)} foot={<><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveCustomer}>Guardar</button></>}>
          <div className="grid-2">
            <Field label="Tipo de identificación"><select className="select" value={edit.id_type} onChange={(e) => setEdit({ ...edit, id_type: e.target.value })}>{["fisica", "juridica", "dimex", "nite", "extranjero"].map((t) => <option key={t} value={t}>{t}</option>)}</select></Field>
            <Field label="Número"><input className="input input--mono" style={{ textAlign: "left" }} value={edit.id_number || ""} onChange={(e) => setEdit({ ...edit, id_number: e.target.value })} /></Field>
          </div>
          <Field label="Nombre o razón social"><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
          <div className="grid-2">
            <Field label="Correo"><input className="input" type="email" value={edit.email || ""} onChange={(e) => setEdit({ ...edit, email: e.target.value })} /></Field>
            <Field label="WhatsApp"><input className="input" value={edit.whatsapp || ""} onChange={(e) => setEdit({ ...edit, whatsapp: e.target.value })} /></Field>
            <Field label="Teléfono"><input className="input" value={edit.phone || ""} onChange={(e) => setEdit({ ...edit, phone: e.target.value })} /></Field>
            <Field label="Divisa preferida"><select className="select" value={edit.currency} onChange={(e) => setEdit({ ...edit, currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field>
          </div>
          <Field label="Dirección (otras señas)"><input className="input" value={edit.address?.senas || ""} onChange={(e) => setEdit({ ...edit, address: { ...(edit.address || {}), senas: e.target.value } })} /></Field>
          <Field label="Notas"><textarea className="textarea" value={edit.notes || ""} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} /></Field>
        </Modal>
      )}
    </>
  );
}
