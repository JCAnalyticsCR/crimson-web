/* Mesa de soporte. Es la pieza que Andres necesita para la propuesta a Hikvision: cada caso con su reloj, su nivel
   (1 a 3) y todo lo que se hizo escrito con hora y autor. Reunion del 13/10 (16:56): "un diseño diferente… que llame
   más la atención y que de verdad yo pueda dar los seguimientos". De ahi la bandeja con semaforo de SLA (el servidor
   decide el grupo; aqui el reloj baja cada 30 s), el proximo paso, el ultimo seguimiento y el seguimiento en 1 clic.
   Los filtros viven en la URL (se comparten y sobreviven al refrescar).
   Empresa aliada (aliado_final §10.6): el ticket se puede asignar a la EMPRESA (su gerente lo reparte en su portal) o
   a una persona de ella; el Responsable lista al equipo de Crimson y, con empresa elegida, a su gente. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { useRecordAutosave } from "../../lib/useRecordAutosave";
import { Empty, Field, I, Icon, Loading, Modal } from "../../ui/components";
import { Lookup, searchAllies, searchCustomers } from "../../ui/Lookup";
import { TicketBoard } from "./TicketBoard";
import { TicketCard } from "./TicketCard";
import { TicketDetail } from "./TicketDetail";
import { ESTADO, KIND, PRIO_LABEL, esAbierto, type Meta, type Ticket } from "./ticketTypes";
import "./tickets.css";

const GRUPOS: { key: string; label: string; color: string }[] = [
  { key: "vencidos", label: "Vencidos", color: "rojo" },
  { key: "por_vencer", label: "Por vencer", color: "ambar" },
  { key: "seguimiento_hoy", label: "Seguimiento para hoy", color: "info" },
  { key: "esperando", label: "Esperando al cliente", color: "gris" },
  { key: "en_tiempo", label: "En tiempo", color: "verde" },
  { key: "cerrados", label: "Resueltos y cerrados", color: "neutro" },
];
const FILTROS: { key: string; label: string; tone?: (n: number) => string }[] = [
  { key: "vencidos", label: "Vencidos", tone: (n) => (n > 0 ? "is-bad" : "is-good") },
  { key: "por_vencer", label: "Por vencer", tone: (n) => (n > 0 ? "is-warn" : "") },
  { key: "sin_responsable", label: "Sin responsable", tone: (n) => (n > 0 ? "is-warn" : "") },
  { key: "seguimiento_hoy", label: "Seguimiento hoy" },
  { key: "sin_movimiento", label: "Sin movimiento +48 h" },
];
const VISTA_KEY = "crimson:tickets:vista";
const leerVista = () => { try { return localStorage.getItem(VISTA_KEY) || ""; } catch { return ""; } };
const guardarVista = (v: string) => { try { localStorage.setItem(VISTA_KEY, v); } catch { /* sin almacenamiento */ } };

const blank = {
  subject: "", customer_id: "", customer_name: "", project_id: "", kind: "soporte", channel: "whatsapp", level: 1, priority: "media", description: "", assigned_to: "", billable: false,
  ally_company_id: "", ally_company: "",
};
/** ally_orig: la empresa con que se abrió (al editar, la empresa solo se manda si cambió: un ticket de un aliado
    suspendido se sigue editando sin tocarla). */
type Form = typeof blank & { id?: number; number?: string; ally_orig?: string };
type AgenteAliado = { id: number; name: string; role: string; company: string | null };
type Proyecto = { id: number; number: string; name: string };

export default function Tickets() {
  const { toast, allows, me } = useSession();
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<Ticket[] | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [open, setOpen] = useState<Ticket | null>(null);
  const [resolverAlAbrir, setResolverAlAbrir] = useState(false);
  const [form, setForm] = useState<Form | null>(null);
  const [proyectos, setProyectos] = useState<Proyecto[]>([]);
  const [agentesAliado, setAgentesAliado] = useState<AgenteAliado[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const openId = useRef<number | null>(null);
  openId.current = open?.id ?? null;

  /* ---------- filtros en la URL (compatibles con los enlaces de antes) ---------- */
  const cliente = params.get("cliente");
  // ?pendiente=fuera_de_tiempo viene del inicio: misma definicion que "Vencidos" (services/sla.py)
  const f = params.get("f") || (params.get("pendiente") === "fuera_de_tiempo" ? "vencidos" : "");
  const asig = params.get("asig") || "";
  const estadoUrl = params.get("estado");
  const estado = estadoUrl || (cliente ? "todos" : "abiertos");
  const tipo = params.get("tipo") || "";
  const prio = params.get("prio") || "";
  const aliado = params.get("aliado") || "";
  const q = params.get("q") || "";
  const vista = params.get("vista") || leerVista() || "bandeja";
  const [qText, setQText] = useState(q);
  const setP = useCallback((patch: Record<string, string | null>) => {
    setParams((cur) => {
      const n = new URLSearchParams(cur);
      // el enlace viejo pasa a ser f=vencidos en el primer cambio (abrir un ticket, buscar, Míos...): sin esto el
      // filtro se perdia y detras del detalle quedaban todos los abiertos
      if (n.get("pendiente") === "fuera_de_tiempo" && !n.has("f") && !("f" in patch)) n.set("f", "vencidos");
      n.delete("pendiente");
      for (const [k, v] of Object.entries(patch)) { if (v) n.set(k, v); else n.delete(k); }
      return n;
    }, { replace: true });
  }, [setParams]);
  useEffect(() => { const t = setTimeout(() => { if (qText !== q) setP({ q: qText.trim() || null }); }, 300); return () => clearTimeout(t); }, [qText]); // eslint-disable-line react-hooks/exhaustive-deps
  const hayFiltros = !!(f || asig || tipo || prio || aliado || q || (estado !== "abiertos" && !cliente));

  const query = useMemo(() => {
    const qs = new URLSearchParams({ orden: "urgencia", limit: "300" });
    if (vista === "tablero") qs.set("status", "tablero");
    else if (cliente && !estadoUrl) qs.set("status", "");
    else if (estado === "abiertos") qs.set("status", "abiertos");
    else if (estado !== "todos") qs.set("status", estado);
    if (cliente) qs.set("customer_id", cliente);
    if (f) qs.set("filtro", f);
    if (asig === "me") qs.set("assigned", "me");
    if (tipo) qs.set("kind", tipo);
    if (prio) qs.set("priority", prio);
    if (aliado) qs.set("ally", aliado);
    if (q) qs.set("q", q);
    return qs.toString();
  }, [vista, cliente, estado, estadoUrl, f, asig, tipo, prio, aliado, q]);

  /* ---------- datos: lista + contadores; refresco cada 120 s sin parpadear ---------- */
  const pedido = useRef(0);
  const load = useCallback(async () => {
    const n = ++pedido.current;
    try {
      const [r, m] = await Promise.all([api<Ticket[]>(`/tickets?${query}`), api<Meta>("/tickets/meta/config")]);
      if (n !== pedido.current) return; // llego tarde: ya se pidio con otros filtros
      setRows(r); setMeta(m);
    } catch (e) { if (n === pedido.current) { setRows((x) => x ?? []); toast(e instanceof Error ? e.message : "Error", "bad"); } }
  }, [query, toast]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, 120_000);
    return () => window.clearInterval(t);
  }, [load]);
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 30_000); return () => window.clearInterval(t); }, []);

  /* ---------- detalle (?id=N: busqueda global, notificaciones, enlaces) ---------- */
  const idParam = params.get("id");
  useEffect(() => {
    if (!idParam) { setOpen(null); return; }
    api<Ticket>(`/tickets/${idParam}`).then(setOpen).catch((e) => toast(e instanceof Error ? e.message : "Error", "bad"));
  }, [idParam, toast]);
  const abrir = (t: Ticket, resolver = false) => { setResolverAlAbrir(resolver); setP({ id: String(t.id) }); };
  const cerrarDetalle = () => { setResolverAlAbrir(false); setP({ id: null }); };
  const cambio = (t: Ticket) => {
    setOpen((o) => (o && o.id === t.id ? t : o));
    setRows((rs) => (rs ? rs.map((x) => (x.id === t.id ? { ...x, ...t } : x)) : rs));
    load();
  };

  const tomar = async (t: Ticket) => {
    if (!me) return;
    try { const r = await api<Ticket>(`/tickets/${t.id}/assign`, { method: "POST", json: { assigned_to: me.user.id } }); toast(`${r.number} es tuyo`); cambio(r); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const mover = async (t: Ticket, status: string) => {
    try { const r = await api<Ticket>(`/tickets/${t.id}`, { method: "PATCH", json: { status } }); toast(`${r.number}: ${ESTADO[status]?.label || status}`); cambio(r); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  /* ---------- formulario Nuevo / Editar (autoguardado T1/T2) ---------- */
  const body = (x: Form) => ({
    subject: x.subject, customer_id: x.customer_id ? Number(x.customer_id) : null, project_id: x.project_id ? Number(x.project_id) : null,
    kind: x.kind, channel: x.channel || null, level: Number(x.level), priority: x.priority, description: x.description || null,
    assigned_to: x.assigned_to ? Number(x.assigned_to) : null, billable: x.billable, contact: {}, tags: [],
    ...(x.id ? (x.ally_company_id !== (x.ally_orig ?? "") ? { ally_company_id: x.ally_company_id ? Number(x.ally_company_id) : null } : {}) : x.ally_company_id ? { ally_company_id: Number(x.ally_company_id) } : {}),
  });
  const formAuto = useRecordAutosave<Form>({
    ns: "tck",
    value: form,
    idOf: (x) => x.id,
    body,
    // editar: se guarda al cerrar (PUT recalcula el SLA y avisa al responsable una sola vez); nuevo: borrador local
    // hasta Guardar (crear gasta el consecutivo TCK, arranca el SLA y avisa)
    save: async (b, id, ctx) => {
      await api<Ticket>(`/tickets/${id}`, { method: "PUT", json: b, headers: ctx.headers, keepalive: ctx.keepalive });
      load();
      if (openId.current === id) api<Ticket>(`/tickets/${id}`).then(setOpen).catch(() => {});
    },
    mode: "close",
    newRecords: "local",
    enabled: !!form && (form.id ? allows("support_desk.editar") : allows("support_desk.crear")),
    label: (x) => (x.id ? x.number || "El ticket" : "El ticket nuevo"),
    apply: (data) => setForm((cur) => (cur ? { ...data, id: cur.id, number: cur.number } : cur)),
  });
  const verProyectos = allows("projects.ver");
  // con empresa aliada elegida, el Responsable suma a su gente (agrupada aparte del equipo de Crimson)
  const aliadaForm = form?.ally_company_id || "";
  useEffect(() => {
    if (!aliadaForm) { setAgentesAliado([]); return; }
    api<AgenteAliado[]>(`/tickets/meta/agents?ally_company_id=${aliadaForm}`).then((r) => setAgentesAliado(r.filter((x) => x.company))).catch(() => setAgentesAliado([]));
  }, [aliadaForm]);
  const elegirAliada = (it: { id: number } | null, text: string) => {
    if (!form) return;
    const id = it ? String(it.id) : "";
    // quien atendia de otra empresa aliada deja de ser responsable (Crimson sigue pudiendo atenderlo)
    const deOtra = !!form.assigned_to && agentesAliado.some((a) => String(a.id) === form.assigned_to) && id !== form.ally_company_id;
    setForm({ ...form, ally_company_id: id, ally_company: text, assigned_to: deOtra ? "" : form.assigned_to });
  };
  useEffect(() => {
    const cid = form?.customer_id;
    if (!cid || !verProyectos) { setProyectos([]); return; }
    api<Proyecto[]>(`/projects?customer_id=${cid}&limit=100`).then(setProyectos).catch(() => setProyectos([]));
  }, [form?.customer_id, verProyectos]);

  const guardarForm = async () => {
    if (!form) return;
    try {
      const t = await formAuto.exclusive(form, (id) => api<Ticket>(id ? `/tickets/${id}` : "/tickets", { method: id ? "PUT" : "POST", json: body(form) }));
      toast(form.id ? "Ticket actualizado" : `Ticket ${t.number} abierto`);
      setForm(null);
      if (form.id) cambio(t); else load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const cancelarForm = () => {
    if (formAuto.isDirty() && !confirm("¿Descartar los cambios?")) return;
    formAuto.discard();
    setForm(null);
  };
  const editar = (t: Ticket) => setForm({
    id: t.id, number: t.number, subject: t.subject, customer_id: t.customer_id ? String(t.customer_id) : "", customer_name: t.customer_id ? t.customer || "" : "",
    project_id: t.project_id ? String(t.project_id) : "", kind: t.kind, channel: t.channel || "", level: t.level, priority: t.priority,
    description: t.description || "", assigned_to: t.assigned_to ? String(t.assigned_to) : "", billable: t.billable,
    ally_company_id: t.ally_company_id ? String(t.ally_company_id) : "", ally_company: t.ally_company || "", ally_orig: t.ally_company_id ? String(t.ally_company_id) : "",
  });

  /* ---------- vista ---------- */
  const lista = rows || [];
  const canFollow = allows("support_desk.seguir|support_desk.editar");
  const canTakeAll = allows("support_desk.editar") && !!meta?.can_see_all;
  const res = meta?.resumen;
  const grupos = useMemo(() => GRUPOS.map((g) => ({ ...g, rows: lista.filter((t) => t.grupo === g.key) })).filter((g) => g.rows.length), [lista]);
  const setVista = (v: string) => { guardarVista(v); setP({ vista: v === "bandeja" ? null : v }); };
  const quitarFiltros = () => { setQText(""); setP({ f: null, asig: null, estado: null, tipo: null, prio: null, aliado: null, q: null }); };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">12 · Soporte</div><h1 className="h1">Tickets</h1></div>
        <div className="page-head__actions">
          {allows("support_desk.crear") && <button className="btn btn--crimson" onClick={() => setForm({ ...blank })}><Icon d={I.plus} />Nuevo ticket</button>}
        </div>
      </div>

      {/* contadores = filtros: un clic aplica, otro clic quita. Salen del servidor con la misma regla que la lista */}
      <div className="eco tk-eco">
        <button type="button" className={`eco__box${!f && estado === "abiertos" ? " is-active" : ""}`} onClick={() => setP({ f: null, estado: null })}>
          <span className="meta">Abiertos</span><b>{res ? res.abiertos : "…"}</b>
        </button>
        {FILTROS.map((x) => {
          const n = res ? (res as unknown as Record<string, number>)[x.key] : null;
          return (
            <button type="button" key={x.key} className={`eco__box ${n !== null && x.tone ? x.tone(n) : ""}${f === x.key ? " is-active" : ""}`} aria-pressed={f === x.key} onClick={() => setP({ f: f === x.key ? null : x.key })}>
              <span className="meta">{x.label}</span><b>{n ?? "…"}</b>
            </button>
          );
        })}
      </div>

      <div className="tk-filters">
        <div className="search"><Icon d={I.search} size={16} /><input placeholder="Asunto o número…" aria-label="Buscar" value={qText} onChange={(e) => setQText(e.target.value)} /></div>
        <button type="button" className={`tk-chip${asig === "me" ? " is-on" : ""}`} aria-pressed={asig === "me"} onClick={() => setP({ asig: asig === "me" ? null : "me" })}>Míos{res ? ` · ${res.asignados_a_mi}` : ""}</button>
        {vista !== "tablero" && (
          <select className="select select--sm" aria-label="Estado" value={estado} onChange={(e) => setP({ estado: e.target.value === "abiertos" && !cliente ? null : e.target.value })}>
            <option value="abiertos">Abiertos</option><option value="todos">Todos</option>
            {(meta?.states || []).map((s) => <option key={s} value={s}>{ESTADO[s]?.label || s}</option>)}
          </select>
        )}
        <select className="select select--sm" aria-label="Tipo" value={tipo} onChange={(e) => setP({ tipo: e.target.value || null })}><option value="">Todo tipo</option>{(meta?.kinds || []).map((k) => <option key={k} value={k}>{KIND[k] || k}</option>)}</select>
        <select className="select select--sm" aria-label="Prioridad" value={prio} onChange={(e) => setP({ prio: e.target.value || null })}><option value="">Toda prioridad</option>{(meta?.priorities || []).map((p) => <option key={p} value={p}>{PRIO_LABEL[p] || p}</option>)}</select>
        {!!meta?.allies?.length && (
          <select className="select select--sm" aria-label="Empresa aliada" title="Tickets de proyectos donde participa ese aliado" value={aliado} onChange={(e) => setP({ aliado: e.target.value || null })}>
            <option value="">Toda empresa aliada</option>
            {meta.allies.map((a) => <option key={a.key} value={a.key}>{a.name} · {a.abiertos}</option>)}
          </select>
        )}
        <div className="tk-filters__end">
          <div className="tabs" role="tablist" aria-label="Vista">
            <button role="tab" aria-selected={vista !== "tablero"} className={vista !== "tablero" ? "is-active" : ""} onClick={() => setVista("bandeja")}>Bandeja</button>
            <button role="tab" aria-selected={vista === "tablero"} className={vista === "tablero" ? "is-active" : ""} onClick={() => setVista("tablero")}>Tablero</button>
          </div>
          <button className="btn btn--ghost btn--sm" onClick={load} aria-label="Refrescar" title="Refrescar"><Icon d={I.refresh} /></button>
        </div>
      </div>

      {rows === null ? <Loading /> : lista.length === 0 ? (
        hayFiltros ? (
          <Empty title="Nada por aquí con estos filtros." action={<button className="btn btn--soft btn--sm" onClick={quitarFiltros}>Quitar filtros</button>} />
        ) : <Empty title="Sin tickets" hint="Cada llamada, garantía o mantenimiento queda aquí con su tiempo de respuesta." />
      ) : vista === "tablero" ? (
        <TicketBoard rows={lista} now={now} onOpen={abrir} onMove={mover} onVerCerrados={() => { guardarVista("bandeja"); setP({ vista: null, estado: "cerrado", f: null }); }} />
      ) : (
        /* Una sola lista con las cabeceras de grupo intercaladas (no una seccion por grupo): cuando el refresco mueve un
           ticket de grupo (por vencer -> vencidos), React mueve la MISMA tarjeta en vez de desmontarla, y el
           seguimiento que se esta escribiendo en ella no se pierde. */
        <div className="tk-inbox">
          {grupos.flatMap((g) => [
            <h2 className="tk-group__head" key={`g-${g.key}`} id={`tk-g-${g.key}`} data-color={g.color}><i style={g.color === "info" ? { background: "var(--info)" } : undefined} />{g.label}<span>· {g.rows.length}</span></h2>,
            ...g.rows.map((t) => (
              <TicketCard key={t.id} t={t} now={now} canFollow={canFollow} canTake={canTakeAll && !t.assigned_to && esAbierto(t)} detailOpen={open?.id === t.id}
                onOpen={() => abrir(t)} onTake={() => tomar(t)} onChanged={cambio} onTrashed={load} />
            )),
          ])}
        </div>
      )}

      {open && (
        <TicketDetail ticket={open} meta={meta} now={now} onClose={cerrarDetalle} onChanged={cambio} onEdit={editar} resolveOnOpen={resolverAlAbrir} />
      )}

      {form && (
        <Modal title={form.id ? `Editar ${form.number || "ticket"}` : "Nuevo ticket"} onClose={() => setForm(null)} wide foot={<>
          {formAuto.bar}
          <button className="btn btn--ghost" onClick={cancelarForm}>Cancelar</button>
          <button className="btn btn--crimson" onClick={guardarForm} disabled={form.subject.trim().length < 3}>Guardar</button>
        </>}>
          <Field label="Asunto"><input className="input" autoFocus value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} placeholder="No graba la cámara del portón" /></Field>
          <div className="grid-3">
            <Field label="Cliente"><Lookup value={form.customer_name} placeholder="Buscar cliente…" fetcher={searchCustomers} onSelect={(it, text) => setForm({ ...form, customer_id: it ? String(it.id) : "", customer_name: text, project_id: it && String(it.id) === form.customer_id ? form.project_id : "" })} /></Field>
            <Field label="Tipo"><select className="select" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{(meta?.kinds || []).map((k) => <option key={k} value={k}>{KIND[k] || k}</option>)}</select></Field>
            <Field label="Canal"><select className="select" value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>{(meta?.channels || []).map((c) => <option key={c} value={c}>{c}</option>)}</select></Field>
            <Field label="Prioridad" hint={meta ? `Primera respuesta: ${meta.sla_horas[form.priority]} h` : undefined}><select className="select" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>{(meta?.priorities || []).map((x) => <option key={x} value={x}>{PRIO_LABEL[x] || x}</option>)}</select></Field>
            <Field label="Nivel" hint="1 atiende · 2 diagnostica · 3 escala al fabricante"><select className="select" value={form.level} onChange={(e) => setForm({ ...form, level: Number(e.target.value) })}><option value={1}>Nivel 1</option><option value={2}>Nivel 2</option><option value={3}>Nivel 3</option></select></Field>
            {meta?.can_see_all && (
              <Field label="Empresa aliada" hint="Opcional. Su gerente lo ve en el portal de aliados y lo reparte.">
                <Lookup value={form.ally_company} placeholder="Crimson (sin aliado)" fetcher={searchAllies} onSelect={elegirAliada} />
              </Field>
            )}
            <Field label="Responsable">
              <select className="select" value={form.assigned_to} onChange={(e) => setForm({ ...form, assigned_to: e.target.value })}>
                <option value="">{form.ally_company_id ? "Sin asignar (lo reparte la empresa aliada)" : "Sin asignar"}</option>
                <optgroup label="Crimson">{(meta?.agents || []).map((a) => <option key={a.id} value={a.id}>{a.name}{a.id === me?.user.id ? " (yo)" : ""}</option>)}</optgroup>
                {agentesAliado.length > 0 && <optgroup label={form.ally_company || "Empresa aliada"}>{agentesAliado.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</optgroup>}
              </select>
            </Field>
            {verProyectos && (
              <Field label="Proyecto (opcional)" hint={form.customer_id ? "Liga el ticket a la obra (y a sus empresas aliadas)." : "Elegí primero el cliente."}>
                <select className="select" value={form.project_id} disabled={!form.customer_id} onChange={(e) => setForm({ ...form, project_id: e.target.value })}>
                  <option value="">Sin proyecto</option>
                  {form.project_id && !proyectos.some((p) => String(p.id) === form.project_id) && <option value={form.project_id}>Proyecto actual</option>}
                  {proyectos.map((p) => <option key={p.id} value={p.id}>{p.number} · {p.name}</option>)}
                </select>
              </Field>
            )}
          </div>
          <Field label="Qué reporta el cliente"><textarea className="textarea" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
        </Modal>
      )}
    </>
  );
}
