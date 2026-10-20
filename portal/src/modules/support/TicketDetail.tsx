/* Detalle del ticket: columna principal (plazo, datos, descripcion) y columna "Seguimiento" (proximo paso, compositor y
   linea de tiempo con notas e historial). Si no se puede escribir, ReadOnlyNotice dice por que (vista previa, rol,
   cerrado o papelera) en vez de esconder los botones en silencio (Andres 16:16). */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, fmtMoney, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { useDirty } from "../../lib/useDirty";
import { useRecordAutosave } from "../../lib/useRecordAutosave";
import { Field, I, Icon, Modal } from "../../ui/components";
import { PhotoStrip } from "../../ui/MediaPicker";
import { ReadOnlyNotice } from "../../ui/ReadOnlyNotice";
import { FollowComposer } from "./FollowComposer";
import { cuandoCR, diaCR, fechaCR, haceCR, localToISO, plazoGrande, semaforoVivo } from "./ticketTime";
import { KIND, PRIO, PRIO_LABEL, esAbierto, type Meta, type Ticket } from "./ticketTypes";

type Resolver = { solution: string; hours: string; billable: boolean; amount: string };
type Visita = { technician_id: string; scheduled_at: string; site: string };

export function TicketDetail({ ticket: t, meta, now, onClose, onChanged, onEdit, resolveOnOpen }: {
  ticket: Ticket; meta: Meta | null; now: number;
  onClose: () => void;
  onChanged: (t: Ticket) => void;
  onEdit: (t: Ticket) => void;
  /** se abrio desde el Tablero soltando la tarjeta en "Resuelto": abre el modal de resolver */
  resolveOnOpen?: boolean;
}) {
  const { allows, toast } = useSession();
  // responsables de la empresa aliada del ticket (asignada o del proyecto): "Responsable" los suma a los internos
  const aliadaId = t.ally_company_id ?? Number((t.allies.find((a) => a.key.startsWith("a:"))?.key || "").slice(2) || 0);
  const [agentesAliado, setAgentesAliado] = useState<{ id: number; name: string; company: string | null }[]>([]);
  useEffect(() => {
    if (!aliadaId || !allows("support_desk.editar")) { setAgentesAliado([]); return; }
    api<{ id: number; name: string; company: string | null }[]>(`/tickets/meta/agents?ally_company_id=${aliadaId}`).then((r) => setAgentesAliado(r.filter((x) => x.company))).catch(() => setAgentesAliado([]));
  }, [aliadaId, allows]);
  const canEdit = allows("support_desk.editar");
  const canFollow = allows("support_desk.seguir|support_desk.editar");
  // el monto que se cobra es precio: el tecnico no lo ve ni lo escribe (13/10, 22:07); lo pone admin o contabilidad
  const verMonto = allows("catalog.precios");
  const [cerrar, setCerrar] = useState<Resolver | null>(null);
  const [visita, setVisita] = useState<Visita | null>(null);
  const [monto, setMonto] = useState<string | null>(null);
  const [pasoFoco, setPasoFoco] = useState(0);
  const log = useRef<HTMLDivElement>(null);
  const abierto = esAbierto(t);
  const bloqueado = !!t.trashed_at || !!t.archived_at;
  const s = semaforoVivo(t.semaforo, t.status, now);

  const nuevoResolver = (): Resolver => ({ solution: t.solution || "", hours: String(t.hours || ""), billable: t.billable, amount: String(t.amount || "") });
  useEffect(() => { if (resolveOnOpen && canEdit && abierto && !bloqueado) setCerrar(nuevoResolver()); }, [resolveOnOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  // Resolver: borrador por ticket en este dispositivo; al sistema solo con "Marcar resuelto" (T4)
  const resAuto = useRecordAutosave<Resolver>({
    ns: "tck-resolver", mode: "never", value: cerrar, idOf: () => t.id, body: (c) => c as unknown as Record<string, unknown>,
    enabled: !!cerrar && canEdit && !bloqueado, label: () => t.number, apply: (c) => setCerrar(c),
  });
  const visitaDirty = useDirty(visita);

  const fail = (e: unknown) => toast(e instanceof Error ? e.message : "Error", "bad");
  const mover = async (st: string, extra: Record<string, unknown> = {}) => {
    try { const r = await api<Ticket>(`/tickets/${t.id}`, { method: "PATCH", json: { status: st, ...extra } }); onChanged(r); toast(st === "en_proceso" && !abierto ? "Ticket reabierto" : "Ticket actualizado"); return r; }
    catch (e) { fail(e); throw e; }
  };
  const resolver = async () => {
    if (!cerrar) return;
    try {
      await resAuto.exclusive(cerrar, () => mover("resuelto", { solution: cerrar.solution, hours: Number(cerrar.hours || 0), billable: cerrar.billable, amount: verMonto ? Number(cerrar.amount || 0) : undefined }));
      setCerrar(null);
    } catch { /* ya avisado */ }
  };
  const cancelarResolver = () => {
    if (resAuto.isDirty() && !confirm("¿Descartar los cambios?")) return;
    resAuto.discard();
    setCerrar(null);
  };
  const asignar = async (uid: number | null) => {
    try { const r = await api<Ticket>(`/tickets/${t.id}/assign`, { method: "POST", json: { assigned_to: uid } }); onChanged(r); toast(uid ? `${t.number} asignado a ${r.assigned}` : "Ticket sin responsable"); }
    catch (e) { fail(e); }
  };
  const guardarMonto = async () => {
    if (monto === null) return;
    try { const r = await api<Ticket>(`/tickets/${t.id}/amount`, { method: "PATCH", json: { amount: Number(monto || 0) } }); onChanged(r); setMonto(null); toast("Monto guardado"); }
    catch (e) { fail(e); }
  };
  /* "Mandar un técnico" arranca con el responsable del ticket (si puede recibir la orden): casi siempre es quien va */
  const nuevaVisita = (): Visita => {
    const resp = t.assigned_to && ((meta?.agents || []).some((a) => a.id === t.assigned_to) || agentesAliado.some((a) => a.id === t.assigned_to)) ? String(t.assigned_to) : "";
    return { technician_id: resp, scheduled_at: "", site: "" };
  };
  const mandarTecnico = async () => {
    if (!visita) return;
    try {
      const r = await api<{ number: string; status?: string }>(`/tickets/${t.id}/work-order`, { method: "POST", json: { technician_id: visita.technician_id ? Number(visita.technician_id) : null, scheduled_at: localToISO(visita.scheduled_at), site: visita.site || null } });
      toast(r.status === "por_programar" ? `Orden ${r.number} creada sin técnico: queda por programar` : `Orden ${r.number} creada`, r.status === "por_programar" ? "bad" : undefined); setVisita(null);
      onChanged(await api<Ticket>(`/tickets/${t.id}`));
    } catch (e) { fail(e); }
  };

  const estado = t.trashed_at ? "en la papelera" : t.archived_at ? "archivado" : t.status === "cerrado" ? `cerrado${t.closed_at ? ` el ${diaCR(t.closed_at)}` : ""}` : null;
  const otra = s.plazo === "respuesta"
    ? (t.resolve_due_at ? `Resolución antes de ${fechaCR(t.resolve_due_at)}` : null)
    : t.first_reply_at ? `Respondido ${fechaCR(t.first_reply_at)}` : null;
  const paso = t.next_step || t.next_step_at;
  const pasoTarde = abierto && t.next_step_vencido;

  type Item = { at: number; key: string; el: ReactNode };
  const items: Item[] = [
    ...(t.notes || []).map((n) => ({
      at: parseTs(n.created_at).getTime(), key: `n${n.id}`,
      el: (
        <li key={`n${n.id}`} className={n.from_client ? "is-client" : n.internal ? "is-internal" : ""}>
          <span className="meta">{n.user} · {haceCR(n.created_at, now)}{n.from_client && <span className="badge badge--info">Cliente</span>}{n.internal && <span className="badge badge--muted">interna</span>}</span>
          <p>{n.body}</p>
          {!!n.photos?.length && <PhotoStrip value={n.photos} onChange={() => {}} disabled />}
        </li>
      ),
    })),
    ...(t.history || []).map((h, i) => ({
      at: parseTs(h.at).getTime(), key: `h${i}`,
      el: <li key={`h${i}`} className="is-event">{fechaCR(h.at)} · {h.user || "Sistema"} · {h.label}</li>,
    })),
  ].sort((a, b) => b.at - a.at); // mas reciente arriba

  return (
    <>
      <Modal title={`${t.number} · ${t.subject}`} onClose={onClose} wide foot={<>
        <span style={{ marginRight: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
          {allows("field.asignar") && !t.work_order_id && abierto && !bloqueado && <button className="btn btn--soft" onClick={() => setVisita(nuevaVisita())}><Icon d={I.check} />Mandar un técnico</button>}
          {t.work_order_id && (t.work_order_status === "por_programar"
            ? <Link className="btn btn--soft" style={{ borderColor: "var(--warn)", color: "var(--warn)" }} to={`/ordenes-trabajo?id=${t.work_order_id}`} title="La visita no tiene técnico: asignala en Órdenes de trabajo">Visita por programar · asignar técnico</Link>
            : <Link className="btn btn--ghost" to={`/ordenes-trabajo?id=${t.work_order_id}`}>Ver la orden</Link>)}
          {/* garantía: Crimson recibe el equipo, lo revisa y, si procede, lo manda al distribuidor (Andrés, 19/10) */}
          {t.customer_id && allows("garantias.ver") && <Link className="btn btn--ghost" to={`/garantias?ticket=${t.id}`}><Icon d={I.shield} />Garantía</Link>}
        </span>
        {canEdit && !bloqueado && <button className="btn btn--ghost" onClick={() => onEdit(t)}>Editar</button>}
        {canEdit && !bloqueado && abierto && <button className="btn btn--crimson" onClick={() => setCerrar(nuevoResolver())}>Resolver</button>}
        {canEdit && !bloqueado && !abierto && <button className="btn btn--soft" onClick={() => mover("en_proceso").catch(() => {})}>Reabrir</button>}
        <button className="btn btn--ghost" onClick={onClose}>Cerrar</button>
      </>}>
        <div className="tk-detail">
          <div className="tk-main">
            <ReadOnlyNotice need="support_desk.seguir|support_desk.editar" what="este ticket" action="responderlo, resolverlo ni reasignarlo" state={estado}
              onReopen={canEdit && !bloqueado && t.status === "cerrado" ? () => { mover("en_proceso").catch(() => {}); } : undefined} />
            <div className="tk-hero" data-color={abierto ? s.color : "neutro"}>
              <b>{abierto ? plazoGrande(s) : t.status === "resuelto" ? `Resuelto ${fechaCR(t.resolved_at)}` : `Cerrado ${fechaCR(t.closed_at || t.resolved_at)}`}</b>
              <small>
                {t.sla ? <>Prometido: {t.sla.respuesta_h ?? "—"} h respuesta · {t.sla.resolucion_h ?? "—"} h resolución · {t.sla.origen_label}{t.sla.contrato ? ` ${t.sla.contrato}` : ""}</> : "Sin SLA registrado (ticket anterior al cambio; se calcula al editarlo)"}
              </small>
              {abierto && otra && <small>{otra}</small>}
            </div>

            <dl className="tk-data">
              <div><dt>Cliente</dt><dd>{t.customer_id ? <Link to={`/clientes/${t.customer_id}`}>{t.customer}</Link> : t.customer ? `${t.customer} (reporte web)` : "Sin ficha"}</dd></div>
              <div><dt>Tipo</dt><dd>{KIND[t.kind] || t.kind}</dd></div>
              <div><dt>Nivel</dt><dd>Nivel {t.level}</dd></div>
              <div><dt>Canal</dt><dd>{t.channel || "—"}</dd></div>
              <div><dt>Prioridad</dt><dd style={{ color: PRIO[t.priority], fontWeight: 700 }}>{PRIO_LABEL[t.priority] || t.priority}</dd></div>
              <div>
                <dt>Responsable</dt>
                <dd>{canEdit && !bloqueado ? (
                  <select className="select" aria-label="Responsable" value={t.assigned_to ?? ""} onChange={(e) => asignar(e.target.value ? Number(e.target.value) : null)}>
                    <option value="">Sin asignar</option>
                    {(meta?.agents || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                    {agentesAliado.map((a) => <option key={`al${a.id}`} value={a.id}>{a.name} · {a.company}</option>)}
                  </select>
                ) : t.assigned || "Sin asignar"}</dd>
              </div>
              <div><dt>Equipo</dt><dd>{t.asset || "—"}</dd></div>
              <div><dt>Proyecto</dt><dd>{t.project_id ? <Link to={`/proyectos/${t.project_id}`}>{t.project || "Ver"}</Link> : "—"}</dd></div>
              <div><dt>Empresa aliada</dt><dd>{t.ally_company ? <b>{t.ally_company}</b> : null}{t.ally_company && t.allies.length ? " · " : ""}{t.allies.length ? t.allies.filter((a) => a.name !== t.ally_company).map((a) => a.name).join(", ") : t.ally_company ? "" : "—"}</dd></div>
              <div><dt>Abierto por</dt><dd>{t.opened_by_name || "—"} · {fechaCR(t.created_at)}</dd></div>
              <div><dt>Horas</dt><dd className="mono">{Number(t.hours || 0)}</dd></div>
              <div><dt>Cobro</dt><dd>{t.billable ? (verMonto ? <span className="money">{fmtMoney(t.amount)}</span> : "Se cobra") : "Sin costo"}</dd></div>
            </dl>

            {/* el tecnico marca "se cobra" sin cifra: el monto lo pone administracion o contabilidad, tambien ya resuelto */}
            {verMonto && meta?.can_charge && t.billable && (
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                {monto === null ? (
                  <button className="btn btn--ghost btn--sm" onClick={() => setMonto(Number(t.amount || 0) > 0 ? String(t.amount) : "")}>{Number(t.amount || 0) > 0 ? "Cambiar el monto cobrado" : "Poner el monto que se cobra"}</button>
                ) : (
                  <>
                    <input className="input input--mono" style={{ maxWidth: 160 }} inputMode="decimal" autoFocus placeholder="Monto cobrado" value={monto} onChange={(e) => setMonto(e.target.value)} />
                    <button className="btn btn--crimson btn--sm" onClick={guardarMonto}>Guardar monto</button>
                    <button className="btn btn--ghost btn--sm" onClick={() => setMonto(null)}>Cancelar</button>
                  </>
                )}
              </div>
            )}

            {t.description && <p className="tk-box">{t.description}</p>}
            {/* reportado desde la pagina web sin ficha de cliente: los datos de contacto viven en el ticket */}
            {t.channel === "web" && t.contact && (
              <p className="tk-box">
                <b>Reporte web</b> · {[t.contact.name, t.contact.company, t.contact.phone, t.contact.email, t.contact.location].filter(Boolean).join(" · ")}
                {t.contact.severidad_percibida ? ` · severidad indicada: ${t.contact.severidad_percibida}` : ""}
              </p>
            )}
            {t.solution && <p className="tk-box" style={{ background: "var(--ok-soft)", border: "1px solid var(--ok)" }}><b>Solución:</b> {t.solution}</p>}
          </div>

          <div className="tk-log" ref={log}>
            <h3 className="h3" style={{ margin: 0 }}>Seguimiento</h3>
            {abierto && (
              <div className={`tk-next${pasoTarde ? " is-late" : ""}`}>
                <b>{paso ? <>→ {t.next_step || "Seguimiento"}{t.next_step_at ? ` · ${cuandoCR(t.next_step_at, now)}` : ""}{pasoTarde ? " · atrasado" : ""}</> : "Sin próximo paso"}</b>
                {canFollow && !bloqueado && <button type="button" className="btn btn--ghost btn--sm" onClick={() => setPasoFoco((n) => n + 1)}>{paso ? "Cambiar" : "Definir"}</button>}
              </div>
            )}
            {canFollow && t.status !== "cerrado" && !bloqueado && (
              // "Cambiar"/"Definir" solo mueven el foco al proximo paso: el compositor no se vuelve a montar (lo escrito sigue)
              <FollowComposer key={t.id} ticket={t} focusNext={pasoFoco} onSaved={onChanged} />
            )}
            {items.length === 0 ? <p className="muted" style={{ margin: 0, fontSize: 13 }}>Sin seguimientos todavía.</p> : <ul className="tk-timeline">{items.map((x) => x.el)}</ul>}
          </div>
        </div>
      </Modal>

      {cerrar && (
        <Modal title={`Resolver ${t.number}`} onClose={() => setCerrar(null)} foot={<>
          {resAuto.bar}
          <button className="btn btn--ghost" onClick={cancelarResolver}>Cancelar</button>
          <button className="btn btn--crimson" onClick={resolver} disabled={cerrar.solution.trim().length < 3}>Marcar resuelto</button>
        </>}>
          <Field label="¿Qué se hizo?" hint="Queda en el historial del equipo y del cliente."><textarea className="textarea" autoFocus value={cerrar.solution} onChange={(e) => setCerrar({ ...cerrar, solution: e.target.value })} placeholder="Se cambió la fuente de 12 V, quedó grabando y se probó con el cliente." /></Field>
          <div className="grid-3">
            <Field label="Horas"><input className="input input--mono" inputMode="decimal" value={cerrar.hours} onChange={(e) => setCerrar({ ...cerrar, hours: e.target.value })} /></Field>
            <Field label="¿Se cobra?"><select className="select" value={cerrar.billable ? "1" : "0"} onChange={(e) => setCerrar({ ...cerrar, billable: e.target.value === "1" })}><option value="0">No (garantía o contrato)</option><option value="1">Sí</option></select></Field>
            {cerrar.billable && verMonto && <Field label="Monto"><input className="input input--mono" inputMode="decimal" value={cerrar.amount} onChange={(e) => setCerrar({ ...cerrar, amount: e.target.value })} /></Field>}
          </div>
        </Modal>
      )}

      {visita && (
        <Modal title="Mandar un técnico al sitio" onClose={() => setVisita(null)} dirty={visitaDirty} onDiscard={() => setVisita(null)} foot={<>
          <button className="btn btn--ghost" onClick={() => setVisita(null)}>Cancelar</button>
          <button className="btn btn--crimson" onClick={mandarTecnico}>Crear orden de trabajo</button>
        </>}>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Se crea con el cliente, el sitio y la descripción de este ticket. El técnico la ve en «Mi día».</p>
          <div className="grid-2">
            <Field label="Técnico" hint={visita.technician_id ? undefined : "Sin técnico la orden queda «por programar» hasta que alguien la asigne."}><select className="select" value={visita.technician_id} onChange={(e) => setVisita({ ...visita, technician_id: e.target.value })}><option value="">Sin asignar</option>{(meta?.agents || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}{agentesAliado.map((a) => <option key={`al${a.id}`} value={a.id}>{a.name} · {a.company}</option>)}</select></Field>
            <Field label="Fecha y hora"><input className="input" type="datetime-local" value={visita.scheduled_at} onChange={(e) => setVisita({ ...visita, scheduled_at: e.target.value })} /></Field>
          </div>
          <Field label="Sitio"><input className="input" value={visita.site} onChange={(e) => setVisita({ ...visita, site: e.target.value })} /></Field>
        </Modal>
      )}
    </>
  );
}
