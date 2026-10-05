/* Accesos de clientes (interno): bandeja de solicitudes de la pagina publica y usuarios del portal por cliente.
   Aprobar = darle a alguien de afuera acceso a los datos de un cliente. El sistema SUGIERE con que cliente
   calza (correo, telefono, cedula), pero quien aprueba elige el cliente y el rol. Solo el admin aprueba. */
import { useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Field, I, Icon, Modal } from "../../ui/components";
import { Lookup, searchCustomers } from "../../ui/Lookup";
import { InviteLink, type InviteOut } from "../cliente/InviteLink";
import "../cliente/cliente.css";

type Sug = { id: number; name: string; id_number: string | null; email: string | null; motivos: string[] };
type Req = {
  id: number; name: string; email: string; phone: string | null; company: string | null; id_number: string | null; message: string | null;
  status: string; customer: string | null; role: string | null; reject_reason: string | null; reviewed_by: string | null; reviewed_at: string | null; created_at: string; suggestions?: Sug[];
};
type PortalUsers = { customer: { id: number; name: string }; users: { id: number; name: string; email: string; role: string; active: boolean; last_login: string | null }[]; invitations: { id: number; email: string; role: string; expires_at: string }[] };

const ROL = { cliente_admin: "Encargado (ve facturas y maneja usuarios)", cliente_usuario: "Usuario (tickets y equipos)" } as Record<string, string>;
const ESTADOS = [{ k: "pendiente", l: "Pendientes" }, { k: "aprobada", l: "Aprobadas" }, { k: "rechazada", l: "Rechazadas" }];

type Approve = { req: Req; mode: "existente" | "nuevo"; customer_id: string; customer_name: string; role: string; nuevo: { name: string; id_type: string; id_number: string; email: string; phone: string } };

/** Ventana de aprobacion a nivel de modulo (nunca dentro de otro componente: se remontaria y perderia el foco). */
function ApproveModal({ a, setA, onDone }: { a: Approve; setA: (a: Approve | null) => void; onDone: (inv: InviteOut, email: string) => void }) {
  const { toast } = useSession();
  const [busy, setBusy] = useState(false);
  const ok = a.mode === "existente" ? !!a.customer_id : a.nuevo.name.trim().length >= 2;
  const aprobar = async () => {
    setBusy(true);
    try {
      const body = a.mode === "existente"
        ? { customer_id: Number(a.customer_id), role: a.role }
        : { role: a.role, new_customer: { name: a.nuevo.name, id_type: a.nuevo.id_type, id_number: a.nuevo.id_number || null, email: a.nuevo.email || null, phone: a.nuevo.phone || null } };
      const inv = await api<InviteOut>(`/access-requests/${a.req.id}/approve`, { method: "POST", json: body });
      onDone(inv, a.req.email);
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo aprobar", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <Modal title={`Aprobar a ${a.req.name}`} onClose={() => setA(null)} foot={<><button className="btn btn--ghost" onClick={() => setA(null)}>Cancelar</button><button className="btn btn--crimson" disabled={!ok || busy} onClick={aprobar}><Icon d={I.check} />Aprobar e invitar</button></>}>
      <p className="muted" style={{ marginTop: 0 }}>Confirme que <b>{a.req.email}</b> pertenece al cliente que elija: va a ver sus tickets, equipos{a.role === "cliente_admin" ? ", cotizaciones y facturas" : ""}.</p>
      <div className="cp-seg">
        <button className={a.mode === "existente" ? "is-on" : ""} onClick={() => setA({ ...a, mode: "existente" })}>Cliente existente</button>
        <button className={a.mode === "nuevo" ? "is-on" : ""} onClick={() => setA({ ...a, mode: "nuevo" })}>Crear cliente</button>
      </div>
      {a.mode === "existente" ? (
        <>
          {!!a.req.suggestions?.length && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
              <span className="meta">Posibles coincidencias (confírmelas):</span>
              {a.req.suggestions.map((s) => (
                <button key={s.id} type="button" className={`cp-row ${String(s.id) === a.customer_id ? "is-on" : ""}`} style={{ cursor: "pointer", textAlign: "left", borderColor: String(s.id) === a.customer_id ? "var(--crimson)" : undefined }} onClick={() => setA({ ...a, customer_id: String(s.id), customer_name: s.name })}>
                  <div className="cp-row__main"><b>{s.name}</b><div className="cp-row__meta">{[s.id_number, s.email].filter(Boolean).join(" · ")}</div></div>
                  <span className="badge badge--info">{s.motivos.join(", ")}</span>
                </button>
              ))}
            </div>
          )}
          <Field label="Cliente"><Lookup value={a.customer_name} placeholder="Buscar cliente…" fetcher={searchCustomers} onSelect={(it, text) => setA({ ...a, customer_id: it ? String(it.id) : "", customer_name: text })} /></Field>
        </>
      ) : (
        <div className="grid-2">
          <Field label="Nombre del cliente"><input className="input" value={a.nuevo.name} onChange={(e) => setA({ ...a, nuevo: { ...a.nuevo, name: e.target.value } })} /></Field>
          <Field label="Tipo de cédula"><select className="select input" value={a.nuevo.id_type} onChange={(e) => setA({ ...a, nuevo: { ...a.nuevo, id_type: e.target.value } })}><option value="juridica">Jurídica</option><option value="fisica">Física</option><option value="dimex">DIMEX</option><option value="extranjero">Extranjero</option></select></Field>
          <Field label="Cédula"><input className="input input--mono" value={a.nuevo.id_number} onChange={(e) => setA({ ...a, nuevo: { ...a.nuevo, id_number: e.target.value } })} /></Field>
          <Field label="Correo de facturación"><input className="input" value={a.nuevo.email} onChange={(e) => setA({ ...a, nuevo: { ...a.nuevo, email: e.target.value } })} /></Field>
          <Field label="Teléfono"><input className="input" value={a.nuevo.phone} onChange={(e) => setA({ ...a, nuevo: { ...a.nuevo, phone: e.target.value } })} /></Field>
        </div>
      )}
      <Field label="Rol en el portal">
        <select className="select input" value={a.role} onChange={(e) => setA({ ...a, role: e.target.value })}>
          {Object.entries(ROL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </Field>
    </Modal>
  );
}

function PortalUsersCard({ allowEdit }: { allowEdit: boolean }) {
  const { toast } = useSession();
  const [cust, setCust] = useState<{ id: string; name: string }>({ id: "", name: "" });
  const [d, setD] = useState<PortalUsers | null>(null);
  const [f, setF] = useState({ email: "", role: "cliente_usuario" });
  const [inv, setInv] = useState<{ inv: InviteOut; email: string } | null>(null);
  const load = useCallback((id: string) => { if (id) api<PortalUsers>(`/customers/${id}/portal`).then(setD).catch((e) => toast(e.message, "bad")); else setD(null); }, [toast]);
  useEffect(() => { load(cust.id); }, [cust.id, load]);
  const invitar = async () => {
    try { const r = await api<InviteOut>(`/customers/${cust.id}/portal/invitations`, { method: "POST", json: f }); setInv({ inv: r, email: f.email }); setF({ ...f, email: "" }); load(cust.id); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo invitar", "bad"); }
  };
  const cambiar = async (uid: number, json: Record<string, unknown>) => {
    try { await api(`/customers/${cust.id}/portal/users/${uid}`, { method: "PATCH", json }); load(cust.id); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const revocar = async (iid: number) => {
    try { await api(`/customers/${cust.id}/portal/invitations/${iid}/revoke`, { method: "POST" }); load(cust.id); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  return (
    <Card title="Usuarios del portal por cliente">
      <div className="cp-form">
        <Lookup value={cust.name} placeholder="Buscar cliente…" fetcher={searchCustomers} onSelect={(it, text) => { setInv(null); setCust({ id: it ? String(it.id) : "", name: text }); }} />
        {d && (
          <>
            {d.users.length === 0 && d.invitations.length === 0 && <span className="muted">Este cliente todavía no tiene usuarios en el portal.</span>}
            <div className="cp-list">
              {d.users.map((u) => (
                <div key={u.id} className="cp-row">
                  <div className="cp-row__main"><b>{u.name}</b><div className="cp-row__meta">{u.email}{u.last_login ? ` · entró ${fmtDate(u.last_login.slice(0, 10))}` : " · no ha entrado"}</div></div>
                  <div className="cp-row__side">
                    {allowEdit ? (
                      <select className="select input" style={{ width: "auto" }} value={u.role} onChange={(e) => cambiar(u.id, { role: e.target.value })}><option value="cliente_admin">Encargado</option><option value="cliente_usuario">Usuario</option></select>
                    ) : <span className="meta">{u.role === "cliente_admin" ? "Encargado" : "Usuario"}</span>}
                    {allowEdit && <button className="btn btn--ghost btn--sm" onClick={() => cambiar(u.id, { active: !u.active })}>{u.active ? "Desactivar" : "Reactivar"}</button>}
                    {!u.active && <span className="badge badge--muted">Desactivado</span>}
                  </div>
                </div>
              ))}
              {d.invitations.map((i) => (
                <div key={`i${i.id}`} className="cp-row">
                  <div className="cp-row__main"><b>{i.email}</b><div className="cp-row__meta">Invitación pendiente · {i.role === "cliente_admin" ? "Encargado" : "Usuario"} · vence {fmtDate(i.expires_at.slice(0, 10))}</div></div>
                  {allowEdit && <button className="btn btn--ghost btn--sm" onClick={() => revocar(i.id)}>Anular</button>}
                </div>
              ))}
            </div>
            {allowEdit && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <input className="input" style={{ flex: "1 1 200px" }} type="email" placeholder="correo@cliente.com" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
                <select className="select input" style={{ flex: "0 1 auto", width: "auto" }} value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option value="cliente_usuario">Usuario</option><option value="cliente_admin">Encargado</option></select>
                <button className="btn btn--crimson" disabled={!/^\S+@\S+\.\S+$/.test(f.email)} onClick={invitar}><Icon d={I.plus} />Invitar</button>
              </div>
            )}
            {inv && <InviteLink inv={inv.inv} email={inv.email} />}
          </>
        )}
      </div>
    </Card>
  );
}

export default function AccessRequests() {
  const { allows, toast } = useSession();
  const puede = allows("portal_clientes.aprobar");
  const [estado, setEstado] = useState("pendiente");
  const [rows, setRows] = useState<Req[] | null>(null);
  const [a, setA] = useState<Approve | null>(null);
  const [rechazo, setRechazo] = useState<{ req: Req; reason: string } | null>(null);
  const [hecho, setHecho] = useState<{ inv: InviteOut; email: string } | null>(null);
  const load = useCallback(() => api<Req[]>(`/access-requests?status=${estado}`).then(setRows).catch((e) => toast(e.message, "bad")), [estado, toast]);
  useEffect(() => { setRows(null); load(); }, [load]);

  const abrir = (r: Req) => {
    const s = r.suggestions?.[0];
    setA({ req: r, mode: "existente", customer_id: s ? String(s.id) : "", customer_name: s?.name || "", role: "cliente_usuario", nuevo: { name: r.company || "", id_type: "juridica", id_number: r.id_number || "", email: "", phone: r.phone || "" } });
  };
  const rechazar = async () => {
    if (!rechazo) return;
    try { await api(`/access-requests/${rechazo.req.id}/reject`, { method: "POST", json: { reason: rechazo.reason } }); toast("Solicitud rechazada"); setRechazo(null); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">Portal del cliente</div><h1 className="h1">Accesos de clientes</h1></div>
      </div>
      <p className="muted" style={{ marginTop: -6, maxWidth: "70ch" }}>Las personas piden acceso desde <b>/solicitar-acceso</b>. Nadie entra por escribir una cédula o un correo: usted confirma a qué cliente pertenece, elige el rol y le llega una invitación.</p>
      {hecho && <div style={{ margin: "12px 0" }}><InviteLink inv={hecho.inv} email={hecho.email} /></div>}
      <div className="cp-grid cp-grid--2" style={{ alignItems: "start", marginTop: 12 }}>
        <Card title="Solicitudes">
          <div className="cp-seg">{ESTADOS.map((e) => <button key={e.k} className={estado === e.k ? "is-on" : ""} onClick={() => setEstado(e.k)}>{e.l}</button>)}</div>
          {rows === null ? <span className="spinner" /> : rows.length === 0 ? <p className="muted">No hay solicitudes {estado === "pendiente" ? "pendientes" : "en esta lista"}.</p> : (
            <div className="cp-list">
              {rows.map((r) => (
                <div key={r.id} className="cp-row" style={{ flexDirection: "column", alignItems: "stretch" }}>
                  <div className="cp-row__main">
                    <b>{r.name} · {r.company}</b>
                    <div className="cp-row__meta">{r.email}{r.phone ? ` · ${r.phone}` : ""}{r.id_number ? ` · céd. ${r.id_number}` : ""} · {fmtDate(r.created_at.slice(0, 10))}</div>
                    {r.message && <p style={{ margin: "6px 0 0", fontSize: 13, overflowWrap: "anywhere" }}>“{r.message}”</p>}
                    {r.status === "pendiente" && !!r.suggestions?.length && <div className="cp-row__meta">Posible cliente: {r.suggestions.map((s) => `${s.name} (${s.motivos.join(", ")})`).join(" · ")}</div>}
                    {r.status === "aprobada" && <div className="cp-row__meta">Aprobada por {r.reviewed_by} → {r.customer} · {r.role === "cliente_admin" ? "Encargado" : "Usuario"}</div>}
                    {r.status === "rechazada" && <div className="cp-row__meta">Rechazada por {r.reviewed_by}: {r.reject_reason}</div>}
                  </div>
                  {r.status === "pendiente" && puede && (
                    <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                      <button className="btn btn--crimson btn--sm" onClick={() => abrir(r)}><Icon d={I.check} size={14} />Revisar y aprobar</button>
                      <button className="btn btn--ghost btn--sm" onClick={() => setRechazo({ req: r, reason: "" })}>Rechazar</button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>
        <PortalUsersCard allowEdit={puede} />
      </div>
      {a && <ApproveModal a={a} setA={setA} onDone={(inv, email) => { setA(null); setHecho({ inv, email }); toast("Solicitud aprobada"); load(); }} />}
      {rechazo && (
        <Modal title={`Rechazar a ${rechazo.req.name}`} onClose={() => setRechazo(null)} foot={<><button className="btn btn--ghost" onClick={() => setRechazo(null)}>Cancelar</button><button className="btn btn--danger" disabled={rechazo.reason.trim().length < 3} onClick={rechazar}>Rechazar</button></>}>
          <Field label="Motivo" hint="Se le envía a la persona por correo (si el envío de correos está activo).">
            <textarea className="textarea" rows={3} value={rechazo.reason} onChange={(e) => setRechazo({ ...rechazo, reason: e.target.value })} placeholder="Ej.: no encontramos un contrato a nombre de esa empresa" />
          </Field>
        </Modal>
      )}
    </>
  );
}
