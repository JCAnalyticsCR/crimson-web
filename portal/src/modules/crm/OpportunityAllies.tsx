/* Grupo B: partes y aliados de la oportunidad.
   Andrés: "Nodo contrata a Crimson, y Crimson contrata a Hauset". El contratante es el cliente al que se cotiza;
   aquí viven las demás empresas, su papel, sus contactos por función y las solicitudes de costo.
   El monto del costo solo lo ve quien ve costos (el servidor ni lo manda a los demás). */
import { useCallback, useState } from "react";
import { ApiError, api, fmtDate, fmtMoney, uploadFile } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, I, Icon } from "../../ui/components";
import { Lookup, type LookupItem } from "../../ui/Lookup";
import DealTerms from "../commissions/DealTerms";
import "./opportunities.css";

export type Contact = { name?: string | null; email?: string | null; phone?: string | null };
export type CostRequest = {
  id: number; participation_id: number; opportunity_id: number; what: string; responsible_id: number | null; responsible: string | null;
  due_date: string | null; status: string; pending: boolean; overdue: boolean; costs_visible: boolean;
  amount?: string | null; currency?: string; valid_until?: string | null; exclusions?: string | null; attachments?: { id: number; url: string; filename: string }[];
  /* respuesta del aliado desde su portal (el desglose solo a quien ve costos) */
  source?: "crimson" | "aliado"; responded_at?: string | null; responded_by?: string | null; lines?: { concept: string; quantity: string; unit: string | null; unit_price: string }[];
};
export type Ally = {
  id: number; opportunity_id: number; project_id: number | null; ally_company_id?: number | null; customer_id: number | null; supplier_id: number | null;
  company_kind: "ally" | "customer" | "supplier" | null; ally_state?: string | null; visible_to_ally?: boolean;
  name: string; role: string; scope: string | null; scope_lines: string[]; contacts: Record<string, Contact>; requests?: CostRequest[];
  /* economia (solo en la ficha del proyecto y para quien ve costos): que recibe el aliado en este negocio */
  deal?: { pago_acordado_crc: string; commission_summary: string | null; commission_general?: boolean; bonus_summary: string | null; committed_date: string | null } | null;
};
export type PendingCost = CostRequest & { ally: string; opportunity_number: string; opportunity_title: string };

export const ALLY_ROLES: Record<string, string> = {
  contratante: "Contratante", referido: "Referido", subcontratista: "Subcontratista", suministro: "Suministro", configuracion: "Configuración", software: "Software",
};
export const CONTACT_FUNCS: Record<string, string> = { comercial: "Comercial", tecnico: "Técnico", aprobador: "Aprobador", pagos: "Pagos" };
const COST_STATES: Record<string, string> = { solicitado: "Solicitado", recibido: "Recibido", aprobado: "Aprobado", rechazado: "Rechazado" };
const STATE_BADGE: Record<string, string> = { solicitado: "warn", recibido: "info", aprobado: "ok", rechazado: "muted" };

/** "Nodo Latam → para Yobel · Sitio X": la misma frase que deja la bitácora al cambiar las partes. */
export const partyLine = (o: { customer: string | null; end_customer?: string | null; site?: string | null }) =>
  `${o.customer || "Sin cliente"}${o.end_customer ? ` → para ${o.end_customer}` : ""}${o.site ? ` · ${o.site}` : ""}`;

/* Empresa de la participacion: una empresa aliada (lo normal, 12:45 "lo ocupo que salgan solo los aliados") o, con
   "¿No es aliado?", un cliente o proveedor como antes. El buscador devuelve ids de tres tablas: se guardan aparte. */
type CompanyKind = "ally" | "customer" | "supplier";
type CompanyRef = { kind: CompanyKind; id: number };
const picked = new Map<number, CompanyRef>();
let seq = 0;
const searchCompanies = (todos: boolean) => async (q: string): Promise<LookupItem[]> => {
  const r = await api<{ kind: CompanyKind; id: number; name: string; hint: string; tax_id?: string | null; suspended?: boolean }[]>(
    `/opportunity-allies/companies?q=${encodeURIComponent(q)}${todos ? "&todos=true" : ""}`,
  );
  return r.map((c) => {
    seq += 1;
    picked.set(seq, { kind: c.kind, id: c.id });
    return { id: seq, label: c.name, hint: [c.hint, c.tax_id, c.suspended ? "suspendido" : null].filter(Boolean).join(" · ") };
  });
};

type AllyForm = { id?: number; company: CompanyRef | null; company_name: string; role: string; scope: string; scope_lines: string[]; contacts: Record<string, Contact>; visible: boolean };
type QuickAlly = { name: string; tax_id: string; func: string; contact: string; existing?: { id: number; name: string } | null };
type ReqForm = {
  id?: number; ally_id: number; ally: string; what: string; responsible_id: string; responsible?: string | null; due_date: string; status: string;
  amount: string; currency: string; valid_until: string; exclusions: string; attachments: { id: number; url: string; filename: string }[];
};

export function AlliesPanel({ oppId, allies, canEdit, seesCosts, sellers, quoteId, onChange }: {
  oppId: number; allies: Ally[]; canEdit: boolean; seesCosts: boolean; sellers: { id: number; name: string }[]; quoteId: number | null;
  onChange: (next: Ally[]) => void;
}) {
  const { toast, allows, me } = useSession();
  const [form, setForm] = useState<AllyForm | null>(null);
  const [req, setReq] = useState<ReqForm | null>(null);
  const [lines, setLines] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [todos, setTodos] = useState(false);
  const [quick, setQuick] = useState<QuickAlly | null>(null);
  const [deal, setDeal] = useState<{ id: number; name: string } | null>(null); // acuerdo economico (economia)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const fetcher = useCallback(searchCompanies(todos), [todos]);
  const reqs = allies.flatMap((a) => a.requests || []);
  const pend = reqs.filter((r) => r.pending);
  const late = reqs.filter((r) => r.overdue);

  const loadLines = () => {
    if (lines !== null || !quoteId || !allows("sales.ver")) return;
    api<{ lines: { name: string }[] }>(`/quotes/${quoteId}`).then((q) => setLines(q.lines.map((l) => l.name))).catch(() => setLines([]));
  };
  const openAlly = (a?: Ally) => {
    loadLines(); setReq(null);
    setQuick(null);
    const ref: CompanyRef | null = a ? (a.ally_company_id ? { kind: "ally", id: a.ally_company_id } : a.supplier_id ? { kind: "supplier", id: a.supplier_id } : a.customer_id ? { kind: "customer", id: a.customer_id } : null) : null;
    setTodos(!!a && a.company_kind !== "ally");
    setForm(a
      ? { id: a.id, company: ref, company_name: a.name, role: a.role, scope: a.scope || "", scope_lines: a.scope_lines, contacts: a.contacts || {}, visible: a.visible_to_ally !== false }
      : { company: null, company_name: "", role: "subcontratista", scope: "", scope_lines: [], contacts: {}, visible: true });
  };
  /* "+ Crear aliado «texto»": alta rapida (nombre, cedula y un contacto por funcion) y queda elegido */
  const crearAliado = async () => {
    if (!quick || !form) return;
    try {
      const contacts = quick.contact.trim() ? { [quick.func]: { name: quick.contact.trim() } } : {};
      const c = await api<{ kind: CompanyKind; id: number; name: string }>("/opportunity-allies/companies", { method: "POST", json: { name: quick.name, tax_id: quick.tax_id || null, contacts } });
      setForm({ ...form, company: { kind: "ally", id: c.id }, company_name: c.name });
      setQuick(null);
      toast(`Aliado ${c.name} creado`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setQuick({ ...quick, existing: (e.data as { existing?: { id: number; name: string } })?.existing || null });
      else toast(e instanceof Error ? e.message : "No se pudo crear", "bad");
    }
  };
  const usarExistente = () => {
    if (!quick?.existing || !form) return;
    setForm({ ...form, company: { kind: "ally", id: quick.existing.id }, company_name: quick.existing.name });
    setQuick(null);
  };
  /* Sin elegir empresa, la API solo liga por vinculo exacto o por la misma cedula. Si hay una empresa aliada con el
     mismo nombre y otra cedula responde 409 con ella: se usa solo si la persona confirma que es la misma. */
  const convertir = (a: Ally) => {
    if (!confirm(`¿Convertir a ${a.name} en empresa aliada? Así podrá ver esta oportunidad en su portal.`)) return;
    const url = `/opportunity-allies/${a.id}/to-ally`;
    run(async () => {
      try {
        return await api<Ally[]>(url, { method: "POST", json: {} });
      } catch (e) {
        const ex = e instanceof ApiError && e.status === 409 ? (e.data as { existing?: { id: number; name: string } })?.existing : null;
        if (!ex) throw e;
        if (!confirm(`Ya existe la empresa aliada «${ex.name}» con ese nombre, pero no con la misma cédula. ¿Es la misma empresa? Aceptar la usa.`)) {
          throw new Error("No se convirtió: elegí la empresa aliada en «Editar» si es otra");
        }
        return await api<Ally[]>(url, { method: "POST", json: { ally_company_id: ex.id } });
      }
    }, `${a.name} ahora es empresa aliada`);
  };
  const run = async (fn: () => Promise<Ally[]>, ok: string) => {
    if (busy) return;
    setBusy(true);
    try { onChange(await fn()); toast(ok); return true; }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); return false; }
    finally { setBusy(false); }
  };

  const saveAlly = async () => {
    if (!form || !form.company) return;
    const k = form.company;
    const json = {
      ally_company_id: k.kind === "ally" ? k.id : null, customer_id: k.kind === "customer" ? k.id : null, supplier_id: k.kind === "supplier" ? k.id : null, role: form.role,
      scope: form.scope.trim() || null, scope_lines: form.scope_lines, contacts: form.contacts, visible_to_ally: form.visible,
    };
    const done = await run(
      () => api<Ally[]>(form.id ? `/opportunity-allies/${form.id}` : `/opportunities/${oppId}/allies`, { method: form.id ? "PUT" : "POST", json }),
      form.id ? "Aliado actualizado" : `${form.company_name} agregado`,
    );
    if (done) setForm(null);
  };
  const removeAlly = (a: Ally) => {
    if (!confirm(`¿Quitar a ${a.name} de la oportunidad?${a.requests?.length ? " Se borran también sus solicitudes de costo." : ""}`)) return;
    run(() => api<Ally[]>(`/opportunity-allies/${a.id}`, { method: "DELETE" }), `${a.name} quitado`);
  };

  const openReq = (a: Ally, r?: CostRequest) => {
    setForm(null);
    setReq(r
      ? { id: r.id, ally_id: a.id, ally: a.name, what: r.what, responsible_id: r.responsible_id ? String(r.responsible_id) : "", responsible: r.responsible, due_date: r.due_date || "", status: r.status,
          amount: r.amount != null ? String(Number(r.amount)) : "", currency: r.currency || "USD", valid_until: r.valid_until || "", exclusions: r.exclusions || "", attachments: r.attachments || [] }
      : { ally_id: a.id, ally: a.name, what: "", responsible_id: me ? String(me.user.id) : "", due_date: "", status: "solicitado", amount: "", currency: "USD", valid_until: "", exclusions: "", attachments: [] });
  };
  const saveReq = async () => {
    if (!req || req.what.trim().length < 2) return;
    const json: Record<string, unknown> = { what: req.what.trim(), responsible_id: req.responsible_id ? Number(req.responsible_id) : null, due_date: req.due_date || null, status: req.status };
    if (seesCosts) Object.assign(json, { amount: req.amount ? Number(req.amount) : null, currency: req.currency, valid_until: req.valid_until || null, exclusions: req.exclusions.trim() || null, attachment_ids: req.attachments.map((x) => x.id) });
    const done = await run(
      () => api<Ally[]>(req.id ? `/opportunity-allies/requests/${req.id}` : `/opportunity-allies/${req.ally_id}/requests`, { method: req.id ? "PUT" : "POST", json }),
      req.id ? "Solicitud actualizada" : `Solicitud de costo a ${req.ally} registrada`,
    );
    if (done) setReq(null);
  };
  const removeReq = (r: CostRequest) => {
    if (!confirm(`¿Eliminar la solicitud «${r.what}»?`)) return;
    run(() => api<Ally[]>(`/opportunity-allies/requests/${r.id}`, { method: "DELETE" }), "Solicitud eliminada");
  };
  const attach = async (f: File | undefined) => {
    if (!f || !req) return;
    try {
      const m = await uploadFile<{ id: number; url: string; filename: string }>("/media", f);
      setReq({ ...req, attachments: [...req.attachments.filter((x) => x.id !== m.id), { id: m.id, url: m.url, filename: m.filename }] });
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo subir el archivo", "bad"); }
  };

  return (
    <section className="ally-panel" aria-label="Aliados">
      <div className="ally-panel__head">
        <b>Aliados y participación</b>
        {canEdit && !form && <button className="btn btn--soft btn--sm" onClick={() => openAlly()}><Icon d={I.plus} />Agregar aliado</button>}
      </div>
      {pend.length > 0 && (
        <div className={`ally-alert${late.length ? " is-late" : ""}`} role="status">
          {pend.length === 1 ? "1 solicitud de costo pendiente" : `${pend.length} solicitudes de costo pendientes`}{late.length ? ` · ${late.length} vencida${late.length > 1 ? "s" : ""}` : ""}
        </div>
      )}
      {allies.length === 0 && !form && <p className="muted" style={{ fontSize: 13, margin: 0 }}>Sin aliados. Agregá quién más participa: referido, subcontratista, suministro, configuración o software.</p>}

      {allies.map((a) => (
        <article className="ally" key={a.id}>
          <div className="ally__head">
            <div className="ally__name">
              <b>{a.name}</b><span className="opp__kind">{ALLY_ROLES[a.role] || a.role}</span>
              <span className="meta">{a.company_kind === "ally" ? "Aliado" : a.company_kind === "supplier" ? "Proveedor" : "Cliente"}</span>
              {a.company_kind === "ally" && a.visible_to_ally === false && <span className="badge badge--muted">Oculta al aliado</span>}
              {a.company_kind === "ally" && a.ally_state && a.ally_state !== "activo" && <span className="badge badge--warn">{a.ally_state}</span>}
            </div>
            {canEdit && (
              <div className="ally__actions">
                {a.company_kind !== "ally" && <button className="btn btn--ghost btn--sm" onClick={() => convertir(a)}>Convertir en aliado</button>}
                <button className="btn btn--ghost btn--sm" onClick={() => openReq(a)}>Solicitar costo</button>
                {allows("commissions.configurar") && <button className="btn btn--ghost btn--sm" onClick={() => setDeal({ id: a.id, name: a.name })}>Acuerdo económico</button>}
                <button className="btn btn--ghost btn--sm" onClick={() => openAlly(a)}>Editar</button>
                <button className="btn btn--ghost btn--sm" onClick={() => removeAlly(a)} aria-label={`Quitar ${a.name}`}><Icon d={I.x} size={14} /></button>
              </div>
            )}
          </div>
          {a.scope && <p className="ally__scope">{a.scope}</p>}
          {a.scope_lines.length > 0 && <div className="ally__chips">{a.scope_lines.map((l) => <span key={l}>{l}</span>)}</div>}
          {Object.keys(a.contacts || {}).length > 0 && (
            <dl className="ally__contacts">
              {Object.entries(a.contacts).map(([k, c]) => (
                <div key={k}><dt>{CONTACT_FUNCS[k] || k}</dt><dd>{[c.name, c.phone, c.email].filter(Boolean).join(" · ")}</dd></div>
              ))}
            </dl>
          )}
          {(a.requests || []).length > 0 && (
            <ul className="ally__reqs">
              {(a.requests || []).map((r) => (
                <li key={r.id} className={r.overdue ? "is-late" : r.pending ? "is-pending" : ""}>
                  <div className="ally__req-top">
                    <b>{r.what}</b>
                    <span className={`badge badge--${r.overdue ? "bad" : STATE_BADGE[r.status] || "info"}`}>{r.overdue ? "Vencida" : COST_STATES[r.status] || r.status}</span>
                  </div>
                  <div className="meta ally__req-meta">
                    <span>{r.responsible || "Sin responsable"}</span>
                    <span>{r.due_date ? `Límite ${fmtDate(r.due_date)}` : "Sin fecha límite"}</span>
                    {r.source === "aliado" && <span>Enviada por el aliado</span>}
                    {r.source !== "aliado" && r.responded_at && <span>Respondida por el aliado el {fmtDate(r.responded_at.slice(0, 10))}</span>}
                    {r.costs_visible && r.amount != null && <span className="money">{fmtMoney(r.amount, r.currency)}</span>}
                    {r.costs_visible && r.valid_until && <span>Vigente hasta {fmtDate(r.valid_until)}</span>}
                  </div>
                  {r.costs_visible && r.exclusions && <p className="ally__scope">Excluye: {r.exclusions}</p>}
                  {r.costs_visible && (r.lines || []).length > 0 && (
                    <ul className="ally__scope" style={{ margin: "4px 0", paddingLeft: 18 }}>
                      {(r.lines || []).map((l, i) => <li key={i}>{l.concept}: {Number(l.quantity)} {l.unit || ""} × {fmtMoney(l.unit_price, r.currency)}</li>)}
                    </ul>
                  )}
                  {r.costs_visible && (r.attachments || []).length > 0 && <div className="ally__chips">{(r.attachments || []).map((x) => <a key={x.id} href={x.url} target="_blank" rel="noreferrer">{x.filename}</a>)}</div>}
                  {canEdit && (seesCosts || !["aprobado", "rechazado"].includes(r.status)) && (
                    <div className="ally__req-actions">
                      <button className="btn btn--ghost btn--sm" onClick={() => openReq(a, r)}>Actualizar</button>
                      <button className="btn btn--ghost btn--sm" onClick={() => removeReq(r)}>Eliminar</button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </article>
      ))}

      {form && (
        <div className="opp-newcust">
          <div className="opp-newcust__head"><b>{form.id ? `Editar aliado · ${form.company_name}` : "Nuevo aliado"}</b><span className="muted">Elegí una empresa aliada o creala aquí.</span></div>
          <div className="grid-2">
            <Field label="Empresa aliada">
              <Lookup
                value={form.company_name}
                placeholder={todos ? "Buscar aliado, cliente o proveedor…" : "Buscar empresa aliada…"}
                fetcher={fetcher}
                onSelect={(it, text) => setForm({ ...form, company: it ? picked.get(it.id) || null : null, company_name: text })}
                onCreate={(text) => setQuick({ name: text, tax_id: "", func: "comercial", contact: "" })}
                createLabel="Crear aliado"
              />
              <button type="button" className="btn btn--ghost btn--sm" style={{ alignSelf: "flex-start", marginTop: 4 }} onClick={() => setTodos((t) => !t)}>{todos ? "Solo empresas aliadas" : "¿No es aliado? Buscar en clientes y proveedores"}</button>
            </Field>
            <Field label="Papel"><select className="select" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{Object.entries(ALLY_ROLES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          </div>
          {quick && (
            <div className="opp-newcust" style={{ margin: "0 0 10px" }}>
              <b>Crear aliado «{quick.name}»</b>
              <div className="grid-3">
                <Field label="Nombre"><input className="input" value={quick.name} onChange={(e) => setQuick({ ...quick, name: e.target.value, existing: null })} /></Field>
                <Field label="Cédula jurídica (opcional)"><input className="input" value={quick.tax_id} onChange={(e) => setQuick({ ...quick, tax_id: e.target.value, existing: null })} /></Field>
                <Field label="Contacto">
                  <select className="select" value={quick.func} onChange={(e) => setQuick({ ...quick, func: e.target.value })}>{Object.entries(CONTACT_FUNCS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                  <input className="input" placeholder="Nombre del contacto" value={quick.contact} onChange={(e) => setQuick({ ...quick, contact: e.target.value })} />
                </Field>
              </div>
              {quick.existing && (
                <p className="muted" style={{ margin: "4px 0" }}>Ya existe: <b>{quick.existing.name}</b>{" "}
                  <button type="button" className="btn btn--soft btn--sm" onClick={usarExistente}>Usar este</button>
                </p>
              )}
              <div className="ally__form-foot">
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setQuick(null)}>Cancelar</button>
                <button type="button" className="btn btn--crimson btn--sm" disabled={quick.name.trim().length < 2} onClick={crearAliado}>Crear aliado</button>
              </div>
            </div>
          )}
          {form.company?.kind === "ally" && (
            <label className="pf-bool" title="El aliado verá esta oportunidad, su alcance y lo que le pidamos">
              <input type="checkbox" checked={form.visible} onChange={(e) => setForm({ ...form, visible: e.target.checked })} />Visible en el portal del aliado
              <span className="meta" style={{ marginLeft: 6 }}>El aliado verá esta oportunidad, su alcance y lo que le pidamos.</span>
            </label>
          )}
          <Field label="Alcance asignado"><textarea className="textarea" style={{ minHeight: 56 }} value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })} placeholder="Tendido y fusión de fibra entre bodegas…" /></Field>
          {lines && lines.length > 0 && (
            <Field label="Partidas de la cotización (opcional)">
              <div className="ally__pick">
                {lines.map((l) => (
                  <label key={l}><input type="checkbox" checked={form.scope_lines.includes(l)} onChange={(e) => setForm({ ...form, scope_lines: e.target.checked ? [...form.scope_lines, l] : form.scope_lines.filter((x) => x !== l) })} />{l}</label>
                ))}
              </div>
            </Field>
          )}
          <details open={form.company?.kind !== "ally"}>
          <summary className="meta" style={{ cursor: "pointer", margin: "6px 0" }}>{form.company?.kind === "ally" ? "Contactos del aliado · editar solo para esta oportunidad" : "Contactos por función"}</summary>
          <div className="ally__contact-grid">
            {Object.entries(CONTACT_FUNCS).map(([k, label]) => {
              const c = form.contacts[k] || {};
              const set = (patch: Contact) => setForm({ ...form, contacts: { ...form.contacts, [k]: { ...c, ...patch } } });
              return (
                <fieldset key={k}>
                  <legend>Contacto {label.toLowerCase()}</legend>
                  <input className="input" placeholder="Nombre" value={c.name || ""} onChange={(e) => set({ name: e.target.value })} />
                  <input className="input" type="email" placeholder="Correo" value={c.email || ""} onChange={(e) => set({ email: e.target.value })} />
                  <input className="input" type="tel" placeholder="Teléfono" value={c.phone || ""} onChange={(e) => set({ phone: e.target.value })} />
                </fieldset>
              );
            })}
          </div>
          </details>
          <div className="ally__form-foot">
            <button className="btn btn--ghost btn--sm" onClick={() => setForm(null)}>Cancelar</button>
            <button className="btn btn--crimson btn--sm" disabled={!form.company || busy} onClick={saveAlly}>Guardar aliado</button>
          </div>
        </div>
      )}

      {req && (
        <div className="opp-newcust">
          <div className="opp-newcust__head"><b>{req.id ? "Actualizar solicitud de costo" : "Solicitud de costo"} · {req.ally}</b><span className="muted">Queda en la bitácora; vencida o sin respuesta cuenta como pendiente.</span></div>
          <Field label="¿Qué se pidió?"><input className="input" autoFocus value={req.what} onChange={(e) => setReq({ ...req, what: e.target.value })} placeholder="Costo de fibra óptica (tendido y fusiones)" /></Field>
          <div className="grid-3">
            <Field label="Responsable">
              <select className="select" value={req.responsible_id} onChange={(e) => setReq({ ...req, responsible_id: e.target.value })}>
                <option value="">Sin asignar</option>
                {me && !sellers.some((s) => s.id === me.user.id) && <option value={me.user.id}>{me.user.full_name}</option>}
                {/* el responsable actual aunque ya no este en la lista (inactivo): si no, el select diria "Sin asignar" */}
                {req.responsible_id && req.responsible_id !== String(me?.user.id) && !sellers.some((s) => String(s.id) === req.responsible_id) && <option value={req.responsible_id}>{req.responsible || `Usuario ${req.responsible_id}`}</option>}
                {sellers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </Field>
            <Field label="Fecha límite"><input className="input" type="date" value={req.due_date} onChange={(e) => setReq({ ...req, due_date: e.target.value })} /></Field>
            <Field label="Estado" hint={seesCosts ? undefined : "Aprobar o rechazar es de quien ve costos."}>
              <select className="select" value={req.status} onChange={(e) => setReq({ ...req, status: e.target.value })}>
                {Object.entries(COST_STATES).filter(([k]) => seesCosts || !["aprobado", "rechazado"].includes(k)).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
          </div>
          {seesCosts && (
            <>
              <div className="grid-3">
                <Field label="Monto"><input className="input input--mono" inputMode="decimal" value={req.amount} onChange={(e) => setReq({ ...req, amount: e.target.value })} placeholder="0.00" /></Field>
                <Field label="Moneda"><select className="select" value={req.currency} onChange={(e) => setReq({ ...req, currency: e.target.value })}><option value="USD">USD</option><option value="CRC">CRC</option></select></Field>
                <Field label="Vigencia"><input className="input" type="date" value={req.valid_until} onChange={(e) => setReq({ ...req, valid_until: e.target.value })} /></Field>
              </div>
              <Field label="Exclusiones"><textarea className="textarea" style={{ minHeight: 48 }} value={req.exclusions} onChange={(e) => setReq({ ...req, exclusions: e.target.value })} placeholder="Sin obra civil, sin permisos municipales…" /></Field>
              <Field label="Adjuntos" hint="PDF o imagen de la oferta del aliado (máx. 10 MB).">
                <div className="ally__chips">
                  {req.attachments.map((x) => (
                    <span key={x.id}><a href={x.url} target="_blank" rel="noreferrer">{x.filename}</a><button type="button" className="ally__chip-x" aria-label={`Quitar ${x.filename}`} onClick={() => setReq({ ...req, attachments: req.attachments.filter((y) => y.id !== x.id) })}>×</button></span>
                  ))}
                  <label className="btn btn--ghost btn--sm"><Icon d={I.upload} size={14} />Adjuntar<input type="file" accept="application/pdf,image/*" hidden onChange={(e) => { attach(e.target.files?.[0]); e.target.value = ""; }} /></label>
                </div>
              </Field>
            </>
          )}
          <div className="ally__form-foot">
            <button className="btn btn--ghost btn--sm" onClick={() => setReq(null)}>Cancelar</button>
            <button className="btn btn--crimson btn--sm" disabled={req.what.trim().length < 2 || busy} onClick={saveReq}>Guardar solicitud</button>
          </div>
        </div>
      )}
      {deal && <DealTerms participationId={deal.id} name={deal.name} onClose={() => setDeal(null)} />}
    </section>
  );
}

/** Solicitudes de costo pendientes (Mis pendientes): las mismas filas que cuenta la tarjeta del inicio. */
export function PendingCosts({ rows, onOpen }: { rows: PendingCost[] | null; onOpen: (oppId: number) => void }) {
  if (rows === null || rows.length === 0) return null;
  return (
    <div className="ally-pending">
      <div className="list-head"><b style={{ fontSize: 14 }}>Solicitudes de costo a aliados</b><span className="muted" style={{ fontSize: 13 }}>Pedidas sin respuesta o recibidas sin aprobar.</span></div>
      <table className="table">
        <thead><tr><th>Límite</th><th>Qué se pidió</th><th>Aliado</th><th>Oportunidad</th><th>Responsable</th><th>Estado</th><th /></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id} style={{ cursor: "pointer" }} onClick={() => onOpen(r.opportunity_id)}>
            <td style={{ whiteSpace: "nowrap", color: r.overdue ? "var(--bad)" : undefined }}>{r.due_date ? fmtDate(r.due_date) : "—"}{r.overdue && <div className="meta" style={{ color: "var(--bad)" }}>Vencida</div>}</td>
            <td style={{ fontWeight: 600 }}>{r.what}</td>
            <td>{r.ally}</td>
            <td><span className="mono muted">{r.opportunity_number}</span> · {r.opportunity_title}</td>
            <td className="muted">{r.responsible || "—"}</td>
            <td><span className={`badge badge--${STATE_BADGE[r.status] || "info"}`}>{COST_STATES[r.status] || r.status}</span></td>
            <td className="num"><button className="btn btn--ghost btn--sm" onClick={(e) => { e.stopPropagation(); onOpen(r.opportunity_id); }}>Abrir</button></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
