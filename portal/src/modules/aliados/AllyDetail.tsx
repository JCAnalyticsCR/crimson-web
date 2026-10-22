/* Ficha del aliado (Administración → Aliados): Datos · Usuarios · En común · Precios · Mensualidad · Números.
   Los usuarios del aliado se invitan AQUÍ (no en Ajustes → Usuarios): aliado comercial o técnico de aliado.
   "Ver como este aliado" abre su portal con sus datos reales, en solo lectura. */
import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import AllyPreviewPicker from "../../app/AllyPreviewPicker";
import { Card, Field, I, Icon } from "../../ui/components";
import { Lookup, searchCustomers, searchProducts, type LookupItem } from "../../ui/Lookup";
import { InviteLink, type InviteOut } from "../cliente/InviteLink";
import { STATE_LABEL, STATE_TONE, searchSuppliers, type AllyRow } from "./Allies";

type Contact = { name?: string | null; email?: string | null; phone?: string | null };
type Detail = AllyRow & {
  legal_name: string | null; contacts: Record<string, Contact>; active: boolean; suspended_at: string | null; customer_id: number | null; customer: { id: number; name: string } | null;
  supplier_id: number | null; supplier: { id: number; name: string } | null; max_users: number | null; notes: string | null; markup_pct: string | null;
  /** reunión 2, pregunta 8: si ve el monto total de la cotización en las oportunidades en común (por defecto no) */
  show_quote_total?: boolean;
  participations: { id: number; role: string; visible_to_ally: boolean; opportunity: { id: number; number: string; title: string; status: string } | null; project: { id: number; number: string; status: string } | null }[];
};
type Users = { users: { id: number; name: string; email: string; role: string; active: boolean; last_login: string | null }[]; invitations: { id: number; email: string; role: string; expires_at: string }[]; seats: { used: number; max: number | null } };
type Price = { id: number; product_id: number; product: { id: number; code: string; name: string; currency: string; price: string } | null; price: string; currency: string; note: string | null };
type Plan = {
  plan: {
    amount: string; currency: string; mode: string; quantity: string | null; product_id: number | null; next_date: string; auto_send: boolean; active: boolean;
    recurrence_id: number; first_run_requires_approval: boolean; last_send_status: string | null; last_send_note: string | null; last_invoice_id: number | null;
  } | null;
  customer: { id: number; name: string } | null; estado: string; vencido: { monto: string; moneda: string; dias: number } | null;
  defaults?: { amount: string | number | null; currency: string; mode: string }; /* Ajustes -> Aliados (plan 1.4) */
  facturas: { id: number; number: string; issue_date: string; due_date: string | null; total: string; balance: string; status: string; currency: string; sent_at: string | null }[];
};
const FUNCIONES: Record<string, string> = { comercial: "Comercial", tecnico: "Técnico", aprobador: "Aprobador", pagos: "Pagos" };
const ROL: Record<string, string> = { aliado: "Aliado comercial", tecnico_aliado: "Técnico de aliado" };
type Tab = "datos" | "usuarios" | "comun" | "precios" | "mensualidad" | "numeros";
const TABS: Tab[] = ["datos", "usuarios", "comun", "precios", "mensualidad", "numeros"];
type FirstInvite = { invite: InviteOut; email: string };

/* Reunión 2 (pedido 14 y pregunta 8): lo que ve el aliado en el detalle de una oportunidad en común. Siempre: etapa,
   próxima acción, su alcance, los seguimientos que se marquen «Visible para el aliado», sus levantamientos y la
   ejecución del proyecto. Nunca costos ni márgenes. El total de la cotización, solo si se habilita aquí. */
function PortalVisibility({ a, edita, reload }: { a: Detail; edita: boolean; reload: () => void }) {
  const { toast } = useSession();
  const [busy, setBusy] = useState(false);
  const cambiar = async (v: boolean) => {
    setBusy(true);
    try { await api(`/allies/${a.id}`, { method: "PUT", json: { show_quote_total: v } }); toast(v ? "Ahora ve el monto total de la cotización" : "Ya no ve el monto de la cotización"); reload(); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo guardar", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <Card title="Qué ve en su portal">
      <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>En cada oportunidad en común ve la etapa, la próxima acción y su fecha, su alcance, los seguimientos que se marquen «Visible para el aliado» en la bitácora, sus levantamientos y la ejecución del proyecto (órdenes y técnicos). Nunca costos, márgenes ni datos de otros aliados.</p>
      <label className="com-check" style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13.5 }}>
        <input type="checkbox" disabled={!edita || busy} checked={!!a.show_quote_total} onChange={(e) => cambiar(e.target.checked)} />
        Ver el monto total de la cotización de sus oportunidades en común
      </label>
    </Card>
  );
}

function Datos({ a, reload }: { a: Detail; reload: () => void }) {
  const { toast, allows } = useSession();
  const [f, setF] = useState({ name: a.name, legal_name: a.legal_name || "", tax_id: a.tax_id || "", email: a.email || "", phone: a.phone || "", max_users: a.max_users ? String(a.max_users) : "", notes: a.notes || "" });
  const [contacts, setContacts] = useState<Record<string, Contact>>(a.contacts || {});
  const [cust, setCust] = useState<LookupItem | null>(a.customer ? { id: a.customer.id, label: a.customer.name } : null);
  const [sup, setSup] = useState<LookupItem | null>(a.supplier ? { id: a.supplier.id, label: a.supplier.name } : null);
  const [motivo, setMotivo] = useState("");
  const edita = allows("aliados.editar");
  const guardar = async () => {
    try {
      await api(`/allies/${a.id}`, { method: "PUT", json: { name: f.name, legal_name: f.legal_name || null, tax_id: f.tax_id || null, email: f.email || null, phone: f.phone || null, max_users: f.max_users ? Number(f.max_users) : null, notes: f.notes || null, contacts, customer_id: cust?.id ?? null, supplier_id: sup?.id ?? null } });
      toast("Aliado guardado"); reload();
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo guardar", "bad"); }
  };
  const estado = async (accion: string, json?: unknown) => {
    if (accion === "deactivate" && !window.confirm(`¿Desactivar a ${a.name}? Sus usuarios dejan de entrar al portal.`)) return;
    try { await api(`/allies/${a.id}/${accion}`, { method: "POST", json: json ?? {} }); toast("Listo"); reload(); } catch (e) { toast(e instanceof Error ? e.message : "No se pudo", "bad"); }
  };
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="Datos de la empresa">
        <div className="grid2" style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
          <Field label="Nombre comercial"><input className="input" disabled={!edita} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Razón social"><input className="input" disabled={!edita} value={f.legal_name} onChange={(e) => setF({ ...f, legal_name: e.target.value })} /></Field>
          <Field label="Cédula jurídica"><input className="input" disabled={!edita} value={f.tax_id} onChange={(e) => setF({ ...f, tax_id: e.target.value })} /></Field>
          <Field label="Correo"><input className="input" disabled={!edita} value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <Field label="Teléfono"><input className="input" disabled={!edita} value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
          <Field label="Tope de usuarios" hint="Vacío = sin tope"><input className="input input--mono" disabled={!edita} inputMode="numeric" value={f.max_users} onChange={(e) => setF({ ...f, max_users: e.target.value })} /></Field>
          <Field label="Cliente de facturación" hint="A quién se le factura la mensualidad"><Lookup value={cust?.label || ""} disabled={!edita} fetcher={searchCustomers} onSelect={(it) => setCust(it)} placeholder="Buscar cliente" /></Field>
          <Field label="Proveedor (para pagarle)"><Lookup value={sup?.label || ""} disabled={!edita} fetcher={searchSuppliers} onSelect={(it) => setSup(it)} placeholder="Buscar proveedor" /></Field>
        </div>
        <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", marginTop: 12 }}>
          {Object.entries(FUNCIONES).map(([k, lbl]) => (
            <Field key={k} label={`Contacto ${lbl.toLowerCase()}`}>
              <input className="input" disabled={!edita} placeholder="Nombre" value={contacts[k]?.name || ""} onChange={(e) => setContacts({ ...contacts, [k]: { ...contacts[k], name: e.target.value } })} />
              <input className="input" disabled={!edita} placeholder="Correo" value={contacts[k]?.email || ""} onChange={(e) => setContacts({ ...contacts, [k]: { ...contacts[k], email: e.target.value } })} />
              <input className="input" disabled={!edita} placeholder="Teléfono" value={contacts[k]?.phone || ""} onChange={(e) => setContacts({ ...contacts, [k]: { ...contacts[k], phone: e.target.value } })} />
            </Field>
          ))}
        </div>
        <Field label="Notas internas (el aliado no las ve)"><textarea className="textarea" rows={2} disabled={!edita} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        {edita && <button className="btn btn--crimson" onClick={guardar}><Icon d={I.check} />Guardar</button>}
      </Card>
      <PortalVisibility a={a} edita={edita} reload={reload} />
      {edita && (
        <Card title="Acceso al portal">
          <p className="muted" style={{ marginTop: 0 }}>Estado: <span className={`badge badge--${STATE_TONE[a.state]}`}>{STATE_LABEL[a.state]}</span>{a.suspended_reason ? ` · ${a.suspended_reason}` : ""}</p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
            {a.state === "activo" && <><input className="input" style={{ flex: "1 1 220px" }} placeholder="Motivo de la suspensión (lo ve el aliado)" value={motivo} onChange={(e) => setMotivo(e.target.value)} /><button className="btn btn--soft" disabled={motivo.trim().length < 3} onClick={() => estado("suspend", { reason: motivo })}>Suspender</button></>}
            {a.state === "suspendido" && <button className="btn btn--soft" onClick={() => estado("reactivate")}>Reactivar</button>}
            {a.active ? <button className="btn btn--ghost" onClick={() => estado("deactivate")}>Desactivar</button> : <button className="btn btn--soft" onClick={() => estado("activate")}>Activar</button>}
          </div>
        </Card>
      )}
    </div>
  );
}

function Usuarios({ a, first, onPreview }: { a: Detail; first?: FirstInvite | null; onPreview?: () => void }) {
  const { toast, allows } = useSession();
  const [d, setD] = useState<Users | null>(null);
  const [f, setF] = useState({ email: "", role: "tecnico_aliado", name: "" });
  const [inv, setInv] = useState<InviteOut | null>(first?.invite ?? null);
  const load = useCallback(() => { api<Users>(`/allies/${a.id}/users`).then(setD).catch(() => setD(null)); }, [a.id]);
  useEffect(load, [load]);
  const puede = allows("aliados.usuarios");
  const invitar = async () => {
    try { setInv(await api<InviteOut>(`/allies/${a.id}/invitations`, { method: "POST", json: { email: f.email, role: f.role, name: f.name || null } })); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo invitar", "bad"); }
  };
  const patch = async (uid: number, json: unknown) => { try { await api(`/allies/${a.id}/users/${uid}`, { method: "PATCH", json }); load(); } catch (e) { toast(e instanceof Error ? e.message : "No se pudo", "bad"); } };
  const revocar = async (iid: number) => { try { await api(`/allies/${a.id}/invitations/${iid}/revoke`, { method: "POST" }); load(); } catch (e) { toast(e instanceof Error ? e.message : "No se pudo", "bad"); } };
  if (!d) return <div className="spinner" />;
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title={`Usuarios · ${d.seats.used}${d.seats.max ? ` de ${d.seats.max}` : ""} cupos`}>
        <table className="table">
          <thead><tr><th>Persona</th><th>Rol</th><th>Estado</th><th /></tr></thead>
          <tbody>
            {d.users.map((u) => (
              <tr key={u.id}>
                <td><b>{u.name}</b><div className="meta">{u.email}{u.last_login ? ` · último ingreso ${fmtDate(u.last_login.slice(0, 10))}` : ""}</div></td>
                <td>{puede ? <select className="select" value={u.role} onChange={(e) => patch(u.id, { role: e.target.value })}>{Object.entries(ROL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select> : ROL[u.role]}</td>
                <td><span className={`badge badge--${u.active ? "ok" : "muted"}`}>{u.active ? "Activo" : "Inactivo"}</span></td>
                <td>{puede && <button className="btn btn--ghost btn--sm" onClick={() => patch(u.id, { active: !u.active })}>{u.active ? "Desactivar" : "Activar"}</button>}</td>
              </tr>
            ))}
            {d.invitations.map((i) => (
              <tr key={`i${i.id}`}><td>{i.email}<div className="meta">Invitación pendiente · vence {fmtDate(i.expires_at.slice(0, 10))}</div></td><td>{ROL[i.role]}</td><td><span className="badge badge--warn">Pendiente</span></td><td>{puede && <button className="btn btn--ghost btn--sm" onClick={() => revocar(i.id)}>Revocar</button>}</td></tr>
            ))}
          </tbody>
        </table>
      </Card>
      {puede && (
        <Card title="Invitar al portal de aliados">
          <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
            <Field label="Correo"><input className="input" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
            <Field label="Nombre (opcional)"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
            <Field label="Rol"><select className="select" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{Object.entries(ROL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          </div>
          <button className="btn btn--crimson" disabled={!f.email.includes("@")} onClick={invitar}><Icon d={I.plus} />Invitar</button>
          {inv && <div style={{ marginTop: 12 }}><InviteLink inv={inv} email={f.email || first?.email || ""} portal="aliados" /></div>}
        </Card>
      )}
      <Card title="Qué ve este aliado en su portal" extra={onPreview && <button className="btn btn--soft btn--sm" onClick={onPreview}><Icon d={I.arrow} />Ver como este aliado</button>}>
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.6 }}>
          <li><b>Solo lo de su empresa.</b> Las oportunidades y proyectos donde participa y que están marcados como visibles (pestaña En común), sus levantamientos, órdenes de trabajo y tickets, el catálogo con su precio de aliado y su mensualidad.</li>
          <li><b>Nunca</b> ve costos ni márgenes de Crimson, otros clientes ni a otros aliados, ni las notas internas de esta ficha.</li>
          <li>El <b>aliado comercial</b> puede invitar a sus técnicos desde su portal (cuentan en los cupos). El <b>técnico de aliado</b> solo ve su trabajo asignado del día.</li>
          <li>Si lo suspendés o desactivás (Datos → Acceso al portal), sus usuarios dejan de entrar al instante.</li>
        </ul>
      </Card>
    </div>
  );
}

/* Incorporacion de un aliado nuevo: lo que falta para que trabaje en la plataforma. Se oculta cuando esta todo. */
function Pasos({ a, go }: { a: Detail; go: (t: Tab) => void }) {
  const { allows } = useSession();
  const pasos: { ok: boolean; parcial?: boolean; txt: string; hint: string; tab: Tab }[] = [
    { ok: !!(a.tax_id && a.email), txt: "Datos de la empresa", hint: a.tax_id && a.email ? "Cédula y correo" : "Falta la cédula jurídica o el correo", tab: "datos" },
    {
      ok: a.users_active > 0, parcial: a.invitations_pending > 0, txt: "Acceso al portal",
      hint: a.users_active > 0 ? `${a.users_active} usuario(s) activo(s)` : a.invitations_pending > 0 ? "Invitación enviada: falta que la acepte" : "Invitá a su primer usuario", tab: "usuarios",
    },
    ...(allows("aliados.mensualidad") ? [{ ok: a.plan.activo, txt: "Mensualidad", hint: a.plan.activo ? "Cobro mensual configurado" : "Configurá el cobro mensual de la plataforma", tab: "mensualidad" as Tab }] : []),
    { ok: a.participations.length > 0, txt: "Trabajo en común", hint: a.participations.length > 0 ? `${a.participations.length} participación(es)` : "Sumalo a una oportunidad (Aliados y participación)", tab: "comun" },
  ];
  if (!a.active || pasos.every((x) => x.ok)) return null;
  const hechos = pasos.filter((x) => x.ok).length;
  return (
    <Card title={`Incorporación · ${hechos} de ${pasos.length}`}>
      <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
        {pasos.map((x) => (
          <button key={x.txt} type="button" className="btn btn--ghost" style={{ justifyContent: "flex-start", textAlign: "left", height: "auto", padding: "8px 10px", whiteSpace: "normal" }} onClick={() => go(x.tab)}>
            <span className={`badge badge--${x.ok ? "ok" : x.parcial ? "warn" : "muted"}`} style={{ marginRight: 8 }}>{x.ok ? "Listo" : x.parcial ? "En curso" : "Falta"}</span>
            <span><b>{x.txt}</b><br /><span className="meta">{x.hint}</span></span>
          </button>
        ))}
      </div>
    </Card>
  );
}

function EnComun({ a, reload }: { a: Detail; reload: () => void }) {
  const { toast, allows } = useSession();
  const visible = async (pid: number, oppId: number, v: boolean) => {
    try {
      // PUT de la participacion con lo que ya tiene (el formulario de la oportunidad manda todo): solo cambia la visibilidad
      const lista = await api<{ id: number; ally_company_id: number | null; role: string; scope: string | null; scope_lines: string[]; contacts: Record<string, Contact> }[]>(`/opportunities/${oppId}/allies`);
      const p = lista.find((x) => x.id === pid);
      if (!p) return;
      await api(`/opportunity-allies/${pid}`, { method: "PUT", json: { ally_company_id: p.ally_company_id, role: p.role, scope: p.scope, scope_lines: p.scope_lines, contacts: p.contacts, visible_to_ally: v } });
      reload();
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo", "bad"); }
  };
  if (!a.participations.length) return <p className="muted">Todavía no participa en ninguna oportunidad. Se suma desde la oportunidad, en «Aliados y participación».</p>;
  return (
    <Card title="Oportunidades y proyectos en común">
      <table className="table">
        <thead><tr><th>Oportunidad</th><th>Proyecto</th><th>Papel</th><th>Visible en el portal del aliado</th></tr></thead>
        <tbody>
          {a.participations.map((p) => (
            <tr key={p.id}>
              <td>{p.opportunity ? <Link to={`/oportunidades?id=${p.opportunity.id}`}>{p.opportunity.number} · {p.opportunity.title}</Link> : "—"}</td>
              <td>{p.project ? <Link to={`/proyectos/${p.project.id}`}>{p.project.number}</Link> : "—"}</td>
              <td>{p.role}</td>
              <td><label className="pf-bool"><input type="checkbox" disabled={!allows("crm_pipeline.editar") || !p.opportunity} checked={p.visible_to_ally} onChange={(e) => p.opportunity && visible(p.id, p.opportunity.id, e.target.checked)} />{p.visible_to_ally ? "Sí" : "No"}</label></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Precios({ a, reload }: { a: Detail; reload: () => void }) {
  const { toast } = useSession();
  const [rows, setRows] = useState<Price[] | null>(null);
  const [prod, setProd] = useState<LookupItem | null>(null);
  const [precio, setPrecio] = useState("");
  const [nota, setNota] = useState("");
  const [mk, setMk] = useState(a.markup_pct ?? "");
  const [prev, setPrev] = useState<{ cost: string | null; cost_currency: string; markup_pct: string; fuente: string | null; precio_aliado: string | null; precio_publico: string; currency: string; estado: string } | null>(null);
  const load = useCallback(() => { api<Price[]>(`/allies/prices?ally_company_id=${a.id}`).then(setRows).catch(() => setRows([])); }, [a.id]);
  useEffect(load, [load]);
  useEffect(() => { if (prod) api<typeof prev>(`/allies/price-preview?product_id=${prod.id}&ally_company_id=${a.id}`).then(setPrev).catch(() => setPrev(null)); }, [prod, a.id]);
  const agregar = async () => {
    try { await api("/allies/prices", { method: "PUT", json: [{ product_id: prod!.id, ally_company_id: a.id, price: precio, note: nota || null }] }); setPrecio(""); setNota(""); load(); toast("Precio guardado"); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo", "bad"); }
  };
  const quitar = async (id: number) => { try { await api(`/allies/prices/${id}`, { method: "DELETE" }); load(); } catch (e) { toast(e instanceof Error ? e.message : "No se pudo", "bad"); } };
  const guardarMk = async () => { try { await api(`/allies/${a.id}`, { method: "PUT", json: { markup_pct: mk === "" ? null : mk } }); toast("Recargo guardado"); reload(); } catch (e) { toast(e instanceof Error ? e.message : "No se pudo", "bad"); } };
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="Recargo propio de este aliado">
        <Field label="Recargo del aliado sobre el costo (%)" hint="Vacío = el recargo por defecto de Aliados"><input className="input input--mono" style={{ maxWidth: 140 }} inputMode="decimal" value={mk} onChange={(e) => setMk(e.target.value)} /></Field>
        <button className="btn btn--soft btn--sm" onClick={guardarMk}>Guardar recargo</button>
      </Card>
      <Card title="Precios fijos para este aliado">
        <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", alignItems: "end" }}>
          <Field label="Producto"><Lookup value={prod?.label || ""} fetcher={searchProducts} onSelect={(it) => setProd(it)} placeholder="Buscar producto" /></Field>
          <Field label="Precio (sin IVA)"><input className="input input--mono" inputMode="decimal" value={precio} onChange={(e) => setPrecio(e.target.value)} /></Field>
          <Field label="Nota"><input className="input" value={nota} onChange={(e) => setNota(e.target.value)} /></Field>
          <button className="btn btn--crimson" disabled={!prod || precio === ""} onClick={agregar}><Icon d={I.plus} />Guardar precio</button>
        </div>
        {prev && <p className="meta">Costo {prev.cost != null ? fmtMoney(prev.cost, prev.cost_currency) : "—"} · recargo {Number(prev.markup_pct)} % · precio aliado {prev.precio_aliado != null ? fmtMoney(prev.precio_aliado, prev.currency) : "a consultar"} ({prev.fuente || "—"}) · público {fmtMoney(prev.precio_publico, prev.currency)}</p>}
        {!rows ? <div className="spinner" /> : rows.length ? (
          <table className="table">
            <thead><tr><th>Producto</th><th>Precio aliado</th><th>Público</th><th>Nota</th><th /></tr></thead>
            <tbody>{rows.map((r) => <tr key={r.id}><td>{r.product?.name}<div className="meta">{r.product?.code}</div></td><td>{fmtMoney(r.price, r.currency)}</td><td>{r.product ? fmtMoney(r.product.price, r.product.currency) : "—"}</td><td>{r.note || "—"}</td><td><button className="btn btn--ghost btn--sm" onClick={() => quitar(r.id)}>Quitar</button></td></tr>)}</tbody>
          </table>
        ) : <p className="muted">Sin precios fijos: se calcula con el recargo.</p>}
      </Card>
    </div>
  );
}

/* plan 1.4: resultado del ultimo envio de la mensualidad y estado real de cada factura */
const SEND_TXT: Record<string, string> = { por_aprobar: "espera tu «Aprobar y enviar»", enviando: "envío sin confirmar (revisala y reenviala)", pendiente: "pendiente de envío", enviada: "enviada", fallida: "no salió", sin_correo: "el cliente no tiene correo" };
const facturaEstado = (x: Plan["facturas"][number]) => (x.status === "anulada" ? "anulada" : Number(x.balance) <= 0 ? "pagada" : !x.sent_at && x.status === "creado" ? "borrador (no enviada)" : x.status === "vencida" || (x.due_date && x.due_date < new Date().toISOString().slice(0, 10)) ? "vencida" : "por pagar");

function Mensualidad({ a, reload }: { a: Detail; reload: () => void }) {
  const { toast, allows } = useSession();
  const [d, setD] = useState<Plan | null>(null);
  const [f, setF] = useState({ amount: "", currency: "CRC", mode: "fija", next_date: new Date().toISOString().slice(0, 10), auto_send: true, active: true, first_run_requires_approval: true });
  const [prod, setProd] = useState<LookupItem | null>(null);
  const load = useCallback(() => {
    api<Plan>(`/allies/${a.id}/plan`).then((x) => {
      setD(x);
      if (x.plan) setF({ amount: String(x.plan.amount ?? ""), currency: x.plan.currency, mode: x.plan.mode, next_date: x.plan.next_date, auto_send: x.plan.auto_send, active: x.plan.active, first_run_requires_approval: x.plan.first_run_requires_approval });
      else if (x.defaults) { const df = x.defaults; setF((f0) => ({ ...f0, amount: df.amount == null ? "" : String(df.amount), currency: df.currency, mode: df.mode })); }
    }).catch(() => setD(null));
  }, [a.id]);
  useEffect(load, [load]);
  const guardar = async () => {
    try { await api(`/allies/${a.id}/plan`, { method: "PUT", json: { ...f, product_id: prod?.id ?? d?.plan?.product_id ?? null, crear_cliente: !a.customer_id } }); toast("Cobro mensual guardado"); load(); reload(); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo", "bad"); }
  };
  const aprobar = async () => {
    if (!d?.plan || !confirm("Le llega la mensualidad al aliado con su PDF y enlace de pago. ¿Enviar?")) return;
    try {
      const r = await api<{ sent: boolean; note: string | null; number: string }>(`/recurrences/${d.plan.recurrence_id}/approve-send`, { method: "POST" });
      toast(r.sent ? `${r.number} enviada; las siguientes salen solas` : r.note || "No salió el correo", r.sent ? undefined : "bad"); load();
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo", "bad"); }
  };
  if (!d) return <div className="spinner" />;
  const pendienteEnvio = d.plan && ["por_aprobar", "enviando", "fallida", "sin_correo"].includes(d.plan.last_send_status || "") && d.facturas.some((x) => x.id === d.plan?.last_invoice_id && !x.sent_at && x.status !== "anulada" && Number(x.balance) > 0);
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="Mensualidad de la plataforma">
        <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
          <Field label="Monto (sin IVA)"><input className="input input--mono" inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          <Field label="Moneda"><select className="select" value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field>
          <Field label="Modo"><select className="select" value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}><option value="fija">Fija por empresa</option><option value="por_usuario">Por usuario activo</option></select></Field>
          <Field label="Próximo cobro"><input className="input" type="date" value={f.next_date} onChange={(e) => setF({ ...f, next_date: e.target.value })} /></Field>
          <Field label="Servicio facturado" hint="Producto de tipo servicio con su CABYS"><Lookup value={prod?.label || ""} fetcher={searchProducts} onSelect={(it) => setProd(it)} placeholder={d.plan?.product_id ? "El configurado" : "Buscar servicio"} /></Field>
        </div>
        <label className="pf-bool"><input type="checkbox" checked={f.auto_send} onChange={(e) => setF({ ...f, auto_send: e.target.checked })} />Enviar la factura automáticamente (PDF y enlace de pago)</label>
        {/* quitar la revisión (que salgan solas) es de quien envía facturas; cambiar el monto o la moneda la vuelve a pedir */}
        {f.auto_send && <label className="pf-bool"><input type="checkbox" checked={f.first_run_requires_approval} disabled={!allows("sales.enviar") && f.first_run_requires_approval && (d.plan?.first_run_requires_approval ?? true)} onChange={(e) => setF({ ...f, first_run_requires_approval: e.target.checked })} />La próxima la reviso yo (Aprobaciones → Mensualidades por aprobar); después salen solas</label>}
        {f.auto_send && d.plan && <p className="meta" style={{ margin: 0 }}>Si cambiás el monto, la moneda o el cliente, la próxima vuelve a esperar tu «Aprobar y enviar».</p>}
        <label className="pf-bool"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} />Cobro activo</label>
        {!a.customer_id && <p className="meta">Se va a crear el cliente de facturación con los datos del aliado.</p>}
        <button className="btn btn--crimson" disabled={f.amount === ""} onClick={guardar}><Icon d={I.check} />Crear o actualizar el cobro mensual</button>
        <p className="meta">Nunca se cobra solo con tarjeta ni se suspende el acceso por atraso: con 30 días de atraso aparece la propuesta en Aprobaciones y la suspensión la decide una persona (Datos → Acceso al portal).</p>
        {d.plan?.last_send_status && <p style={{ margin: 0, fontSize: 13 }}>Último envío: <b>{SEND_TXT[d.plan.last_send_status] || d.plan.last_send_status}</b>{d.plan.last_send_note ? ` · ${d.plan.last_send_note}` : ""}
          {pendienteEnvio && allows("sales.enviar") && <> <button className="btn btn--crimson btn--sm" onClick={aprobar}>{d.plan.last_send_status === "por_aprobar" ? "Aprobar y enviar" : "Reenviar"}</button></>}</p>}
      </Card>
      <Card title={`Estado: ${d.estado === "al_dia" ? "al día" : "vencida"}`}>
        {d.vencido && <p><span className="badge badge--bad">Vencida</span> {fmtMoney(d.vencido.monto, d.vencido.moneda)} · {d.vencido.dias} día(s)</p>}
        {d.facturas.length ? (
          <table className="table"><thead><tr><th>Factura</th><th>Emitida</th><th>Total</th><th>Saldo</th><th>Estado</th></tr></thead>
            <tbody>{d.facturas.map((x) => <tr key={x.id}><td><Link to={`/facturas/${x.id}`}>{x.number}</Link></td><td>{fmtDate(x.issue_date)}</td><td>{fmtMoney(x.total, x.currency)}</td><td>{fmtMoney(x.balance, x.currency)}</td><td>{facturaEstado(x)}</td></tr>)}</tbody>
          </table>
        ) : <p className="muted">Todavía no hay facturas de mensualidad.</p>}
      </Card>
    </div>
  );
}

export default function AllyDetail() {
  const { id } = useParams();
  const { allows, me } = useSession();
  const [sp] = useSearchParams();
  const first = (useLocation().state as FirstInvite | null) ?? null; // la invitacion hecha al crear el aliado
  const [a, setA] = useState<Detail | null>(null);
  const [tab, setTab] = useState<Tab>(TABS.includes(sp.get("tab") as Tab) ? (sp.get("tab") as Tab) : "datos");
  const [preview, setPreview] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => { api<Detail>(`/allies/${id}`).then(setA).catch((e) => setErr(e instanceof Error ? e.message : "No se pudo cargar")); }, [id]);
  useEffect(load, [load]);
  if (err) return <p className="muted">{err}</p>;
  if (!a) return <div className="spinner" />;
  const tabs: [Tab, string, boolean][] = [
    ["datos", "Datos", true],
    ["usuarios", "Usuarios", true],
    ["comun", "En común", true],
    ["precios", "Precios", allows("aliados.precios") && allows("catalog.costos")],
    ["mensualidad", "Mensualidad", allows("aliados.mensualidad")],
    ["numeros", "Números", allows("commissions.ver_todo")],
  ];
  return (
    <>
      <div className="page-head">
        <div><div className="meta"><Link to="/aliados">Aliados</Link> · {a.tax_id || "sin cédula"}</div><h1 className="h1">{a.name} <span className={`badge badge--${STATE_TONE[a.state]}`}>{STATE_LABEL[a.state]}</span></h1></div>
        <div className="page-head__actions">
          {me?.role === "admin" && <button className="btn btn--soft" onClick={() => setPreview(true)}><Icon d={I.arrow} />Ver como este aliado</button>}
        </div>
      </div>
      <Pasos a={a} go={setTab} />
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "16px 0" }}>
        {tabs.filter(([, , ok]) => ok).map(([k, l]) => <button key={k} className={`btn btn--sm ${tab === k ? "btn--crimson" : "btn--ghost"}`} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {tab === "datos" && <Datos a={a} reload={load} />}
      {tab === "usuarios" && <Usuarios a={a} first={first} onPreview={me?.role === "admin" ? () => setPreview(true) : undefined} />}
      {tab === "comun" && <EnComun a={a} reload={load} />}
      {tab === "precios" && <Precios a={a} reload={load} />}
      {tab === "mensualidad" && <Mensualidad a={a} reload={load} />}
      {tab === "numeros" && (
        <Card title="Números de la alianza">
          <p className="muted" style={{ marginTop: 0 }}>Lo que recibe el aliado (acuerdos, comisiones, bonos y pagos) y lo que la alianza le deja a Crimson se ven en <Link to="/comisiones">Comisiones</Link>. Aquí: {a.proyectos_en_comun} proyecto(s) en común, mensualidad {a.plan.activo ? (a.plan.al_dia ? "al día" : "vencida") : "sin configurar"}.</p>
        </Card>
      )}
      {preview && <AllyPreviewPicker role="aliado" company={{ id: a.id, name: a.name }} onClose={() => setPreview(false)} />}
    </>
  );
}
