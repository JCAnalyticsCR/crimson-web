/* Ordenes de trabajo. Dos pantallas en una:
   - El tecnico ve "mi dia" y trabaja con botones grandes: llegue, iniciar jornada, terminar jornada, finalizar.
   - El supervisor asigna, programa y revisa. Al finalizar, el material usado sale de inventario una sola vez.
   Reunion del 13/10 (30:48-31:13): el panel "Tiempo" ya no depende de ser el tecnico (el admin no veia los botones),
   el tiempo va por jornada y por persona, y el detalle se guarda solo (antes el admin perdia todo al hacer clic
   fuera y "Recibido por" no se guardaba nunca).
   Empresa aliada (aliado_final §10.6): con proyecto, los técnicos salen agrupados (Crimson / cada aliado que participa) y
   la orden se puede asignar a la EMPRESA aliada aunque no tenga técnico todavía (su gerente la reparte en su portal).
   Al cambiar el equipo se manda también la empresa elegida: sin ella la API la deduce de los técnicos. */
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { useRecordAutosave } from "../../lib/useRecordAutosave";
import { Card, Empty, Field, I, Icon, Modal, Loading } from "../../ui/components";
import { Lookup, searchCustomers, searchProducts } from "../../ui/Lookup";
import { PhotoStrip } from "../../ui/MediaPicker";
import { ArchiveActions } from "../../ui/ArchiveActions";
import { ReadOnlyNotice } from "../../ui/ReadOnlyNotice";
import { TrashButton } from "../../ui/TrashButton";
import { InviteTechButton } from "./InviteTech";
import { WorkOrderTime, enCurso, type Crew, type Running, type TimeEntry } from "./WorkOrderTime";

type Material = { id?: number; product_id: number | null; name: string; quantity: string; unit: string; planned: string };
type Task = { text: string; done?: boolean };
export type Order = {
  id: number; number: string; title: string; kind: string; status: string; site: string | null; scheduled_at: string | null;
  archived_at?: string | null; trashed_at?: string | null;
  customer_id: number | null; customer: string | null; project_id: number | null; project: string | null;
  technician_id: number | null; technician: string | null; helpers: number[]; arrived_at: string | null; started_at: string | null;
  finished_at: string | null; tasks: Task[]; photos: string[]; notes: string | null; customer_signature: string | null;
  stock_applied: boolean; hours: number | null; materials?: Material[]; warehouse_id?: number | null;
  hours_source?: "registro" | "legado" | null; running?: Running[]; stale?: boolean; crew?: Crew[];
  hours_estimated?: number | null; hours_estimated_label?: string | null; time_entries?: TimeEntry[];
  /** solo del portal: ids de material que trajo el servidor (lo que ya no esta se manda como "removed") */
  _ids?: number[];
  /** empresa aliada que ejecuta la orden (la toma de su tecnico aliado; la API valida que participe en el proyecto) */
  ally_company_id?: number | null; ally_company?: string | null;
};
type Today = { today: Order[]; next: Order[]; surveys: { id: number; number: string; kind_label: string; site: string | null }[] };
/* company: tecnicos de empresas aliadas que participan en el proyecto (/field/technicians?project_id=) */
type Tech = { id: number; name: string; role: string; company?: string | null; ally_company_id?: number | null };

const STATUS: Record<string, { label: string; tone: string }> = {
  asignada: { label: "Asignada", tone: "info" }, en_sitio: { label: "En sitio", tone: "warn" }, en_proceso: { label: "En proceso", tone: "warn" },
  finalizada: { label: "Finalizada", tone: "ok" }, cancelada: { label: "Cancelada", tone: "muted" },
};
const KINDS: Record<string, string> = { instalacion: "Instalación", visita: "Visita", mantenimiento: "Mantenimiento", soporte: "Soporte" };
const CERRADAS = ["finalizada", "cancelada"];

/* Costa Rica es UTC-6 todo el año (no hay horario de verano). La agenda se escribe y se lee en hora de aquí,
   aunque la computadora del usuario tenga mal la zona: si no, un trabajo de las 9 a. m. aparece a las 3 p. m. */
const CR_OFFSET = "-06:00";
const toISO = (local: string) => (local ? new Date(`${local}:00${CR_OFFSET}`).toISOString() : null);
const toLocalInput = (iso: string | null) => (iso ? new Date(parseTs(iso).getTime() - 6 * 3600_000).toISOString().slice(0, 16) : "");
const hhmm = (s: string | null) => (s ? parseTs(s).toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit", timeZone: "America/Costa_Rica" }) : "—");
const dayLabel = (s: string | null) => (s ? parseTs(s).toLocaleDateString("es-CR", { weekday: "short", day: "2-digit", month: "short", timeZone: "America/Costa_Rica" }) : "Sin fecha");
const blank = { title: "", kind: "instalacion", project_id: "", customer_id: "", customer_name: "", site: "", scheduled_at: "", technician_id: "", helpers: [] as number[], warehouse_id: null as number | null, notes: "", tasks: [] as Task[], materials: [] as Material[], ally_company_id: "" };
type Form = typeof blank & {
  id?: number; number?: string; /** solo del portal: nombres del equipo (ayudantes dados de baja) */ crew?: Crew[];
  /** solo del portal: nombre de la empresa aliada que ya tenia la orden (aunque hoy no tenga tecnicos en la lista) */ ally_company?: string | null;
};
/** Empresas aliadas que se pueden elegir: las de los tecnicos del proyecto (participan en el) y la que ya tenia. */
const empresasDe = (techs: Tech[], actual?: { id: string; name?: string | null }) => {
  const m = new Map<string, string>();
  for (const t of techs) if (t.ally_company_id && t.company) m.set(String(t.ally_company_id), t.company);
  if (actual?.id && !m.has(actual.id)) m.set(actual.id, actual.name || "Empresa aliada actual");
  return [...m.entries()].map(([id, name]) => ({ id, name }));
};
/** Opciones de tecnico agrupadas: Crimson y cada empresa aliada del proyecto. */
const opcionesTecnicos = (techs: Tech[]) => {
  const grupos = new Map<string, Tech[]>();
  for (const t of techs) {
    const k = t.company || "Crimson";
    grupos.set(k, [...(grupos.get(k) || []), t]);
  }
  return [...grupos.entries()]
    .sort(([a], [b]) => (a === "Crimson" ? -1 : b === "Crimson" ? 1 : a.localeCompare(b)))
    .map(([g, ts]) => <optgroup key={g} label={g}>{ts.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</optgroup>);
};
/** Si cambio el equipo, va tambien la empresa elegida (sin ella la API la deduce de los tecnicos). */
const conEmpresa = (diff: Record<string, unknown>, b: Record<string, unknown>) =>
  ("technician_id" in diff || "helpers" in diff) && !("ally_company_id" in diff) ? { ...diff, ally_company_id: b.ally_company_id } : diff;
const num = (v: string | number | null | undefined) => Number(String(v ?? "").replace(",", ".")) || 0;

/** Lo que trae el servidor, con los ids de material de referencia (para mandar "removed"). */
const withIds = (o: Order): Order => ({ ...o, _ids: (o.materials || []).map((m) => m.id).filter((x): x is number => typeof x === "number") });
/** Respuesta del servidor a una accion que no toca lo que se escribe (tiempo, asignacion): no pisa lo escrito. */
const keepEdits = (local: Order | null, server: Order): Order =>
  local && local.id === server.id
    ? { ...server, tasks: local.tasks, photos: local.photos, notes: local.notes, customer_signature: local.customer_signature, materials: local.materials, _ids: local._ids }
    : withIds(server);

/* Lo que se autoguarda del detalle (O1): tareas, usado por id, fotos, notas y "Recibido por" (antes nunca se mandaba). */
const progressBody = (o: Order) => ({
  tasks: o.tasks || [],
  photos: o.photos || [],
  notes: o.notes || "",
  customer_signature: o.customer_signature || "",
  quantities: Object.fromEntries((o.materials || []).filter((m) => m.id).map((m) => [String(m.id), num(m.quantity)])),
  removed: (o._ids || []).filter((id) => !(o.materials || []).some((m) => m.id === id)),
});
/** Solo lo que difiere del ultimo cuerpo aceptado: si el tecnico (celular) y el supervisor (oficina) tienen el detalle
    abierto, cada uno manda lo que toco y el usado va por id; solo chocan si tocan el mismo campo (gana el ultimo). */
const progressDiff = (b: Record<string, unknown>, prev: Record<string, unknown> | null) => {
  const pv = prev || {};
  const diff: Record<string, unknown> = {};
  for (const k of ["tasks", "photos", "notes", "customer_signature"]) if (JSON.stringify(b[k]) !== JSON.stringify(pv[k])) diff[k] = b[k];
  const q = b.quantities as Record<string, number>, pq = (pv.quantities || {}) as Record<string, number>;
  const dq = Object.fromEntries(Object.entries(q).filter(([k, v]) => pq[k] !== v));
  if (Object.keys(dq).length) diff.quantities = dq;
  const rm = (b.removed as number[]).filter((x) => !((pv.removed as number[] | undefined) || []).includes(x));
  if (rm.length) diff.removed = rm;
  return diff;
};
/** Editar (O2): solo las claves que cambiaron desde que se abrio el formulario (o desde el ultimo guardado). Mientras
    el supervisor cambia el sitio, el tecnico puede marcar tareas, anotar o quitar material desde el celular: mandar el
    formulario completo pisaria eso con la copia de cuando se abrio. El PUT no toca lo que no viene. */
const formDiff = (b: Record<string, unknown>, prev: Record<string, unknown> | null) =>
  prev ? Object.fromEntries(Object.entries(b).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(prev[k]))) : b;

export default function WorkOrders() {
  const { toast, allows, me } = useSession();
  const [params, setParams] = useSearchParams();
  const [day, setDay] = useState<Today | null>(null);
  const [allSt, setAll] = useState<Order[] | null>(null); // null = cargando
  const all = allSt ?? [];
  const [tab, setTab] = useState<"dia" | "todas">("dia");
  const [open, setOpen] = useState<Order | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [techs, setTechs] = useState<Tech[]>([]);
  const [busy, setBusy] = useState(false);
  const [asg, setAsg] = useState<{ technician_id: string; helpers: number[]; scheduled_at: string } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const puedeAsignar = allows("field.asignar");

  const load = useCallback(() => {
    api<Today>("/work-orders/meta/today").then(setDay).catch(() => setDay(null));
    api<Order[]>("/work-orders?limit=200").then(setAll).catch(() => setAll([]));
  }, []);
  useEffect(() => { load(); }, [load]);
  // con proyecto se suman los tecnicos de las empresas aliadas que participan en el (agrupados: Crimson / aliado)
  const proyecto = form?.project_id || (open?.project_id ? String(open.project_id) : "");
  useEffect(() => {
    api<Tech[]>(proyecto ? `/field/technicians?project_id=${proyecto}` : "/field/technicians").then(setTechs).catch(() => setTechs([]));
  }, [proyecto]);
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 60_000); return () => window.clearInterval(t); }, []);

  const enEquipo = !!open && !!me && (open.technician_id === me.user.id || (open.helpers || []).includes(me.user.id));
  const abierta = !!open && !CERRADAS.includes(open.status) && !open.trashed_at;
  const puedeRegistrar = !!open && allows("field.editar") && (enEquipo || puedeAsignar) && abierta;

  /* ---------- autoguardado del detalle (O1): tras cada pausa, solo lo que cambio ---------- */
  const auto = useRecordAutosave<Order>({
    ns: "ot",
    mode: "pause",
    value: open,
    idOf: (o) => o.id,
    body: progressBody,
    save: async (b, id, ctx) => {
      const diff = progressDiff(b, ctx.prev);
      if (!Object.keys(diff).length) return; // nada distinto: no se llama
      await api(`/work-orders/${id}/progress`, { method: "POST", json: diff, headers: ctx.headers, keepalive: ctx.keepalive });
    },
    enabled: puedeRegistrar,
    label: (o) => o.number,
    apply: (data) => setOpen((cur) => (cur && cur.id === data.id ? { ...cur, tasks: data.tasks, photos: data.photos, notes: data.notes, customer_signature: data.customer_signature, materials: data.materials } : cur)),
  });

  const show = useCallback(async (id: number) => {
    const o = await api<Order>(`/work-orders/${id}`);
    setAsg(null);
    setOpen(withIds(o));
  }, []);
  useEffect(() => { const id = params.get("id"); if (id) show(Number(id)).catch((e) => toast(e instanceof Error ? e.message : "Error", "bad")); }, [params, show, toast]);
  const cerrar = () => { setOpen(null); setAsg(null); if (params.get("id")) setParams({}); };

  /* Lo escrito que el servidor todavia no tiene (tareas, usado, fotos, notas, recibido por), solo lo que cambio desde el
     ultimo guardado aceptado. Se calcula DENTRO de exclusive (ya termino cualquier autoguardado en curso): mandar el
     cuerpo completo pisaria lo que el tecnico anoto desde el celular con la copia vieja de este detalle. */
  const pendiente = (o: Order) => (auto.enabled ? progressDiff(progressBody(o), auto.accepted()) : {});
  /* Botones del panel Tiempo: mandan tambien lo escrito pendiente y nunca corren a la par del autoguardado. La
     respuesta del servidor pasa a ser la referencia. */
  const act = async (action: string, extra: Record<string, unknown> = {}) => {
    if (!open) return;
    setBusy(true);
    try {
      const o = await auto.exclusive(open, () => api<Order>(`/work-orders/${open.id}/${action}`, { method: "POST", json: { ...pendiente(open), ...extra } }));
      auto.fromServer();
      setOpen(withIds(o));
      load();
      toast(action === "finish" ? "Orden finalizada; el material usado salió de inventario." : action === "start" ? "Jornada iniciada" : action === "pause" ? "Jornada terminada" : "Guardado");
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  /* Tiempo y asignacion: tampoco corren a la par del autoguardado; primero sube lo pendiente y la respuesta no pisa
     lo que se esta escribiendo. */
  const run = async (fn: () => Promise<Order>, ok: string) => {
    if (!open) return false;
    setBusy(true);
    try {
      const actual = open;
      const o = await auto.exclusive(actual, async () => {
        const cambios = pendiente(actual);
        if (Object.keys(cambios).length) await api(`/work-orders/${actual.id}/progress`, { method: "POST", json: cambios });
        return fn();
      });
      setOpen((cur) => keepEdits(cur, o));
      load();
      toast(ok);
      return true;
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); return false; }
    finally { setBusy(false); }
  };
  const guardarAsignacion = async () => {
    if (!open || !asg) return;
    const ok = await run(() => api<Order>(`/work-orders/${open.id}/assign`, { method: "PATCH", json: { technician_id: asg.technician_id ? Number(asg.technician_id) : null, helpers: asg.helpers, scheduled_at: toISO(asg.scheduled_at) } }), "Asignación guardada");
    if (ok) setAsg(null);
  };

  const setMat = (i: number, patch: Partial<Material>) => setOpen((o) => (o ? { ...o, materials: (o.materials || []).map((m, j) => (j === i ? { ...m, ...patch } : m)) } : o));
  const setTask = (i: number, done: boolean) => setOpen((o) => (o ? { ...o, tasks: o.tasks.map((t, j) => (j === i ? { ...t, done } : t)) } : o));

  /* ---------- formulario Asignar / Editar (O2): nueva = borrador local; existente = se guarda al cerrar ---------- */
  const formBody = (f: Form) => ({
    title: f.title, kind: f.kind, project_id: f.project_id ? Number(f.project_id) : null, customer_id: f.customer_id ? Number(f.customer_id) : null,
    site: f.site || null, scheduled_at: toISO(f.scheduled_at),
    technician_id: f.technician_id ? Number(f.technician_id) : null, helpers: f.helpers, warehouse_id: f.warehouse_id, tasks: f.tasks, notes: f.notes || null,
    ally_company_id: f.ally_company_id ? Number(f.ally_company_id) : null,
    materials: f.materials.filter((m) => m.name.trim()).map((m) => ({ id: m.id, product_id: m.product_id, name: m.name, quantity: num(m.quantity), unit: m.unit || "Unid", planned: num(m.planned) })),
  });
  const formAuto = useRecordAutosave<Form>({
    ns: "ot-form",
    mode: "close",
    newRecords: "local",
    value: form,
    idOf: (f) => f.id,
    body: formBody,
    // solo lo que cambio; el PUT conserva lo que el tecnico ya anoto como usado (filas por id)
    save: async (b, id, ctx) => {
      const diff = conEmpresa(formDiff(b, ctx.prev), b);
      if (!Object.keys(diff).length) return; // nada distinto: no se llama
      await api(`/work-orders/${id}`, { method: "PUT", json: diff, headers: ctx.headers, keepalive: ctx.keepalive });
      load();
    },
    enabled: !!form && puedeAsignar,
    label: (f) => (f.id ? f.number || "La orden" : "La orden nueva"),
    apply: (data) => setForm((cur) => (cur ? { ...data, id: cur.id, number: cur.number, crew: cur.crew } : cur)),
  });
  const save = async () => {
    if (!form) return;
    try {
      // nueva: el cuerpo completo; existente: lo distinto del ultimo guardado aceptado, calculado dentro de exclusive
      // (ya termino cualquier autoguardado en curso)
      await formAuto.exclusive(form, async (id) => {
        if (!id) return api<Order>("/work-orders", { method: "POST", json: formBody(form) });
        const diff = conEmpresa(formDiff(formBody(form), formAuto.accepted()), formBody(form));
        return Object.keys(diff).length ? api<Order>(`/work-orders/${id}`, { method: "PUT", json: diff }) : undefined;
      });
      toast("Orden guardada"); setForm(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const cancelarForm = () => {
    if (formAuto.isDirty() && !confirm("¿Descartar los cambios?")) return;
    formAuto.discard();
    setForm(null);
  };
  const editar = async () => {
    if (!open) return;
    await auto.flush(); // lo escrito en el detalle llega antes de abrir el formulario
    setForm({
      id: open.id, number: open.number, title: open.title, kind: open.kind, project_id: open.project_id ? String(open.project_id) : "", customer_id: open.customer_id ? String(open.customer_id) : "",
      customer_name: open.customer || "", site: open.site || "", scheduled_at: toLocalInput(open.scheduled_at), technician_id: open.technician_id ? String(open.technician_id) : "",
      helpers: open.helpers || [], crew: open.crew, warehouse_id: open.warehouse_id ?? null, notes: open.notes || "", tasks: open.tasks || [],
      materials: (open.materials || []).map((m) => ({ ...m, quantity: String(m.quantity), planned: String(m.planned) })),
      ally_company_id: open.ally_company_id ? String(open.ally_company_id) : "", ally_company: open.ally_company ?? null,
    });
    setOpen(null);
  };
  /* Editar el texto de tareas no borra lo que el tecnico ya marco: la tarea con el mismo texto conserva su "hecha". */
  const tareasDesde = (txt: string, antes: Task[]) => {
    const hechas = new Map(antes.map((t) => [t.text.trim(), !!t.done]));
    return txt.split("\n").filter((x) => x.trim()).map((x) => ({ text: x.trim(), done: hechas.get(x.trim()) ?? false }));
  };

  const insignias = (o: Order) => (
    <>
      {!o.technician_id && !CERRADAS.includes(o.status) && <span className="badge badge--warn">Sin técnico</span>}
      {o.ally_company && <span className="badge badge--info">Aliado: {o.ally_company}</span>}
      {enCurso(o.running, now) && <span className="badge badge--info">{enCurso(o.running, now)}</span>}
    </>
  );
  const jobCard = (o: Order) => (
    <button className="job" key={o.id} onClick={() => show(o.id)}>
      <span className="job__when">{o.scheduled_at ? hhmm(o.scheduled_at) : "—"}<br />{dayLabel(o.scheduled_at).split(",")[0]}</span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <b>{o.title}</b>
        <small>{o.customer || "Sin cliente"}{o.site ? ` · ${o.site}` : ""}</small>
      </span>
      <span style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>{insignias(o)}<span className={`badge badge--${STATUS[o.status]?.tone || "muted"}`}>{STATUS[o.status]?.label || o.status}</span></span>
    </button>
  );

  /* Sin esto el trabajo se cierra con "instalé las cámaras" en una nota y no queda respaldo de nada. */
  const faltaAnotar = (open?.materials || []).filter((m) => num(m.planned) > 0 && !(num(m.quantity) > 0)).map((m) => m.name);
  const editable = puedeRegistrar;
  const estadoOT = open ? (open.trashed_at ? "en la papelera" : open.status === "finalizada" ? `finalizada${open.finished_at ? ` el ${dayLabel(open.finished_at)}` : ""}` : open.status === "cancelada" ? "cancelada" : null) : null;
  const faltaAsignar = !!open && puedeAsignar && abierta && (!open.technician_id || !open.scheduled_at);
  /* Con field.editar pero sin ser del equipo ni poder asignar (p. ej. vista previa como Tecnico de una orden ajena):
     los botones no salen y se dice por que, en vez de dejar la pantalla "solo para ver" sin explicacion (13/10, 16:16). */
  const alcanceOT = open && abierta && allows("field.editar") && !enEquipo && !puedeAsignar
    ? (open.technician ? `Esta orden es de ${open.technician}: solo el equipo de la orden registra tiempo y avance.` : "Esta orden todavía no tiene técnico: solo el equipo de la orden registra tiempo y avance, y la asigna un supervisor.")
    : null;
  const asgVal = asg ?? (open ? { technician_id: open.technician_id ? String(open.technician_id) : "", helpers: open.helpers || [], scheduled_at: toLocalInput(open.scheduled_at) } : null);
  const chipsAyudantes = (sel: number[], tecnico: string, onChange: (v: number[]) => void, crew?: Crew[]) => (
    <div className="chips">
      {techs.filter((t) => String(t.id) !== tecnico).map((t) => {
        const on = sel.includes(t.id);
        return <button type="button" key={t.id} className={`chip${on ? " is-on" : ""}`} aria-pressed={on} onClick={() => onChange(on ? sel.filter((x) => x !== t.id) : [...sel, t.id])}>{t.name}</button>;
      })}
      {/* ya estaba en la orden pero no sale en /field/technicians (dado de baja o cambio de rol): se ve y se puede quitar */}
      {sel.filter((id) => String(id) !== tecnico && !techs.some((t) => t.id === id)).map((id) => {
        const c = crew?.find((x) => x.id === id);
        return <button type="button" key={id} className="chip is-on" aria-pressed title="Ya no puede recibir órdenes: tocá para quitarlo" onClick={() => onChange(sel.filter((x) => x !== id))}>{c?.name || "Ayudante"}{c?.active === false ? " · inactivo" : ""}</button>;
      })}
      {techs.length === 0 && <span className="muted" style={{ fontSize: 12.5 }}>Sin técnicos en la empresa.</span>}
    </div>
  );

  return (
    <>
      <div className="page-head">
        <div><div className="meta">08 · Campo</div><h1 className="h1">Órdenes de trabajo</h1></div>
        <div className="page-head__actions">
          <div className="tabs"><button className={tab === "dia" ? "is-active" : ""} onClick={() => setTab("dia")}>Mi día</button><button className={tab === "todas" ? "is-active" : ""} onClick={() => setTab("todas")}>Todas</button></div>
          {puedeAsignar && <button className="btn btn--crimson" onClick={() => setForm({ ...blank })}><Icon d={I.plus} />Asignar orden</button>}
        </div>
      </div>

      {tab === "dia" ? (
        !day ? <div style={{ padding: 40, textAlign: "center" }}><span className="spinner" /></div> : (
          <>
            <Card title={`Hoy · ${day.today.length}`}>
              {day.today.length === 0 ? <p className="muted" style={{ fontSize: 13.5, margin: 0 }}>No hay trabajos programados para hoy.</p> : day.today.map(jobCard)}
            </Card>
            <Card title={`Próximos · ${day.next.length}`}>
              {day.next.length === 0 ? <p className="muted" style={{ fontSize: 13.5, margin: 0 }}>Nada pendiente. Buen trabajo.</p> : day.next.map(jobCard)}
            </Card>
            {day.surveys.length > 0 && (
              <Card title="Levantamientos sin enviar">
                {day.surveys.map((s) => <Link className="job" key={s.id} to={`/levantamientos?id=${s.id}`}><span style={{ flex: 1 }}><b>{s.number} · {s.kind_label}</b><small>{s.site || "Sin sitio"}</small></span><span className="badge badge--warn">Borrador</span></Link>)}
              </Card>
            )}
          </>
        )
      ) : (
        <Card flush>
          {allSt === null ? <Loading /> : all.length === 0 ? <Empty hint="Cada visita e instalación se programa como orden de trabajo y deja su evidencia." /> : (
            <table className="table">
              <thead><tr><th>Número</th><th>Trabajo</th><th>Cliente</th><th>Proyecto</th><th>Técnico</th><th>Programada</th><th>Estado</th><th /></tr></thead>
              <tbody>{all.map((o) => (
                <tr key={o.id}>
                  <td className="mono muted">{o.number}</td><td style={{ fontWeight: 600 }}>{o.title}</td><td className="muted">{o.customer || "—"}</td>
                  <td className="mono muted">{o.project || "—"}</td><td className="muted">{o.technician || "Sin asignar"}</td>
                  <td style={{ fontSize: 13 }}>{o.scheduled_at ? `${dayLabel(o.scheduled_at)} ${hhmm(o.scheduled_at)}` : "—"}</td>
                  <td><span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}><span className={`badge badge--${STATUS[o.status]?.tone || "muted"}`}>{STATUS[o.status]?.label || o.status}</span>{insignias(o)}</span></td>
                  <td className="num row-actions"><span className="row-actions__in"><button className="btn btn--ghost btn--sm" onClick={() => show(o.id)}>Abrir</button><TrashButton kind="work_order" id={o.id} number={o.number} onDone={load} /></span></td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </Card>
      )}

      {open && (
        <Modal title={`${open.number} · ${open.title}`} onClose={cerrar} wide foot={<>
          {auto.bar}
          {puedeAsignar && open.status !== "finalizada" && <button className="btn btn--ghost" style={{ marginRight: "auto" }} onClick={editar}>Editar</button>}
          <ArchiveActions kind="work_order" id={open.id} number={open.number} archivedAt={open.archived_at} trashedAt={open.trashed_at} onDone={() => { cerrar(); load(); }} />
          <button className="btn btn--ghost" onClick={cerrar}>Cerrar</button>
        </>}>
          <ReadOnlyNotice need="field.editar" what="esta orden de trabajo" action="registrar tiempo ni avance" state={estadoOT} scope={alcanceOT} />
          <div className="eco" style={{ marginBottom: 14 }}>
            <div className="eco__box"><span className="meta">Estado</span><b style={{ fontSize: 15 }}>{STATUS[open.status]?.label || open.status}</b></div>
            <div className="eco__box"><span className="meta">Llegada</span><b style={{ fontSize: 15 }}>{hhmm(open.arrived_at)}</b></div>
            <div className="eco__box"><span className="meta">Inicio</span><b style={{ fontSize: 15 }}>{hhmm(open.started_at)}</b></div>
            <div className="eco__box"><span className="meta">Horas (h·persona)</span><b>{open.hours ?? "—"}</b></div>
          </div>
          <p className="muted" style={{ fontSize: 13.5, marginTop: 0 }}>
            {KINDS[open.kind] || open.kind} · {open.customer || "Sin cliente"}{open.site ? ` · ${open.site}` : ""}
            {open.project && <> · <Link to={`/proyectos/${open.project_id}`}>{open.project}</Link></>}
            {open.technician ? ` · Técnico: ${open.technician}` : ""}
            {(open.crew || []).filter((c) => c.role === "ayudante").length > 0 && ` · Ayudantes: ${(open.crew || []).filter((c) => c.role === "ayudante").map((c) => c.name).join(", ")}`}
          </p>

          {/* asignacion rapida: sin tecnico la orden no le aparece a nadie en "Mi dia" (la OT que nace del proyecto viene asi) */}
          {faltaAsignar && asgVal && (
            <Card title="Asignación rápida">
              {!open.technician_id && <p style={{ margin: "0 0 10px", fontSize: 13, color: "var(--warn)" }}>Sin técnico, esta orden no le aparece a nadie en «Mi día».</p>}
              <div className="grid-2">
                <Field label="Técnico"><select className="select" value={asgVal.technician_id} onChange={(e) => setAsg({ ...asgVal, technician_id: e.target.value, helpers: asgVal.helpers.filter((x) => String(x) !== e.target.value) })}><option value="">Sin asignar</option>{opcionesTecnicos(techs)}</select></Field>
                <Field label="Fecha y hora"><input className="input" type="datetime-local" value={asgVal.scheduled_at} onChange={(e) => setAsg({ ...asgVal, scheduled_at: e.target.value })} /></Field>
              </div>
              <Field label="Ayudantes">{chipsAyudantes(asgVal.helpers, asgVal.technician_id, (helpers) => setAsg({ ...asgVal, helpers }), open.crew)}</Field>
              <div style={{ display: "flex", justifyContent: "flex-end" }}><button className="btn btn--crimson btn--sm" disabled={busy || !asg} onClick={guardarAsignacion}>Guardar asignación</button></div>
            </Card>
          )}

          <WorkOrderTime order={open} meId={me?.user.id} puedeRegistrar={puedeRegistrar} puedeAsignar={puedeAsignar} busy={busy} faltaAnotar={faltaAnotar} onAct={act}
            run={(fn, ok) => run(fn as () => Promise<Order>, ok)} />

          {open.tasks?.length > 0 && (
            <Card title="Tareas">
              {open.tasks.map((t, i) => (
                <label className={`task${t.done ? " is-done" : ""}`} key={i}>
                  <input type="checkbox" checked={!!t.done} disabled={!editable} onChange={(e) => setTask(i, e.target.checked)} />
                  <span>{t.text}</span>
                </label>
              ))}
            </Card>
          )}

          <Card title="Material usado" flush extra={faltaAnotar.length > 0 && abierta ? <span className="badge badge--warn">Falta anotar {faltaAnotar.length}</span> : undefined}>
            {(open.materials || []).length === 0 ? <p className="muted" style={{ fontSize: 13, padding: "0 18px 14px" }}>Sin materiales planificados.</p> : (
              <table className="table">
                <thead><tr><th>Material</th><th className="num">Planificado</th><th className="num">Usado</th></tr></thead>
                <tbody>{(open.materials || []).map((m, i) => (
                  <tr key={m.id ?? i}>
                    <td style={{ fontWeight: 600 }}>{m.name}</td>
                    <td className="num mono muted">{Number(m.planned)} {m.unit}</td>
                    <td className="num">{open.stock_applied || !editable ? <span className="mono">{Number(m.quantity)}</span> : (
                      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end", alignItems: "center" }}>
                        <input className="input input--mono" style={{ maxWidth: 110, borderColor: num(m.planned) > 0 && !(num(m.quantity) > 0) ? "var(--warn)" : undefined }} inputMode="decimal" aria-label={`Usado de ${m.name}`} value={m.quantity} onChange={(e) => setMat(i, { quantity: e.target.value })} />
                        <button className="btn btn--ghost btn--sm" title="No se usó en este trabajo" onClick={() => setOpen({ ...open, materials: (open.materials || []).filter((_, j) => j !== i) })}><Icon d={I.x} size={13} /></button>
                      </div>
                    )}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </Card>

          <Field label="Fotografías de la instalación" hint="Van al informe técnico de entrega que recibe el cliente.">
            <PhotoStrip value={open.photos || []} onChange={(photos) => setOpen({ ...open, photos })} disabled={!editable} />
          </Field>
          <Field label="Notas del trabajo"><textarea className="textarea" value={open.notes || ""} disabled={!editable} onChange={(e) => setOpen({ ...open, notes: e.target.value })} placeholder="Qué se hizo, qué quedó pendiente, qué encontró el técnico." /></Field>
          <Field label="Recibido por (nombre del cliente)" hint="Queda en el informe de entrega.">
            <input className="input" maxLength={160} value={open.customer_signature || ""} disabled={!editable} onChange={(e) => setOpen({ ...open, customer_signature: e.target.value })} />
          </Field>
          {open.stock_applied && <p className="muted" style={{ fontSize: 12.5 }}>El inventario de esta orden ya se descontó; por eso no se puede reescribir.</p>}
        </Modal>
      )}

      {form && (
        <Modal title={form.id ? `Editar ${form.number || "orden"}` : "Asignar orden de trabajo"} onClose={() => setForm(null)} wide foot={<>
          {formAuto.bar}
          <button className="btn btn--ghost" onClick={cancelarForm}>Cancelar</button>
          <button className="btn btn--crimson" onClick={save} disabled={form.title.trim().length < 2}>Guardar</button>
        </>}>
          <div className="grid-3">
            <Field label="Trabajo"><input className="input" autoFocus value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Instalación de 8 cámaras" /></Field>
            <Field label="Tipo"><select className="select" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{Object.entries(KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            <Field label="Técnico" hint={allows("settings.configurar") ? "¿No está en la lista? Invitalo con + Técnico." : undefined}><div style={{ display: "flex", gap: 6 }}><select className="select" value={form.technician_id} onChange={(e) => {
              // un tecnico de una empresa aliada deja la orden en esa empresa
              const t = techs.find((x) => String(x.id) === e.target.value);
              setForm({ ...form, technician_id: e.target.value, helpers: form.helpers.filter((x) => String(x) !== e.target.value), ally_company_id: t?.ally_company_id ? String(t.ally_company_id) : form.ally_company_id });
            }}><option value="">Sin asignar</option>{opcionesTecnicos(techs)}</select><InviteTechButton /></div></Field>
            <Field label="Cliente"><Lookup value={form.customer_name} placeholder="Buscar cliente…" fetcher={searchCustomers} onSelect={(it, text) => setForm({ ...form, customer_id: it ? String(it.id) : "", customer_name: text })} /></Field>
            <Field label="Fecha y hora"><input className="input" type="datetime-local" value={form.scheduled_at} onChange={(e) => setForm({ ...form, scheduled_at: e.target.value })} /></Field>
            <Field label="Sitio"><input className="input" value={form.site} onChange={(e) => setForm({ ...form, site: e.target.value })} /></Field>
          </div>
          {(form.ally_company_id || (form.project_id && empresasDe(techs).length > 0)) && (
            <Field label="Empresa aliada" hint="La ejecuta un aliado que participa en el proyecto: la ve en su portal y su gerente la reparte si no tiene técnico.">
              <select className="select" value={form.ally_company_id} onChange={(e) => {
                const id = e.target.value;
                // un tecnico aliado de OTRA empresa no queda en la orden (la API no deja dos empresas en un trabajo)
                const deOtra = (uid: string | number) => { const t = techs.find((x) => String(x.id) === String(uid)); return !!t?.ally_company_id && String(t.ally_company_id) !== id; };
                setForm({ ...form, ally_company_id: id, technician_id: form.technician_id && deOtra(form.technician_id) ? "" : form.technician_id, helpers: form.helpers.filter((h) => !deOtra(h)) });
              }}>
                <option value="">Crimson (sin aliado)</option>
                {empresasDe(techs, { id: form.ally_company_id, name: form.ally_company }).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </Field>
          )}
          <Field label="Ayudantes">{chipsAyudantes(form.helpers, form.technician_id, (helpers) => setForm({ ...form, helpers }), form.crew)}</Field>
          <Field label="Tareas" hint="Una por línea; el técnico las va marcando (las ya marcadas se conservan).">
            <textarea className="textarea" value={form.tasks.map((t) => t.text).join("\n")} onChange={(e) => setForm({ ...form, tasks: tareasDesde(e.target.value, form.tasks) })} placeholder={"Montar cámaras\nCorrer cable\nConfigurar grabador"} />
          </Field>
          <Card title="Materiales planificados" extra={<button className="btn btn--soft btn--sm" onClick={() => setForm({ ...form, materials: [...form.materials, { product_id: null, name: "", quantity: "0", unit: "Unid", planned: "1" }] })}><Icon d={I.plus} />Agregar</button>} flush>
            {form.materials.length === 0 ? <p className="muted" style={{ fontSize: 13, padding: "0 18px 14px" }}>Lo que el técnico saca de bodega para este trabajo.</p> : (
              <table className="table">
                <thead><tr><th>Material</th><th>Inventario</th><th className="num">Planificado</th><th /></tr></thead>
                <tbody>{form.materials.map((m, i) => (
                  <tr key={m.id ?? `n${i}`}>
                    <td><Lookup value={m.name} placeholder="Buscar en el catálogo…" fetcher={searchProducts} onSelect={(pr, text) => setForm({ ...form, materials: form.materials.map((x, j) => (j === i ? { ...x, product_id: pr ? pr.id : null, name: text } : x)) })} /></td>
                    <td className="muted" style={{ fontSize: 12.5 }}>{m.product_id ? "Descuenta de bodega" : "Sin código"}</td>
                    <td className="num"><input className="input input--mono" style={{ maxWidth: 110 }} inputMode="decimal" value={m.planned} onChange={(e) => setForm({ ...form, materials: form.materials.map((x, j) => (j === i ? { ...x, planned: e.target.value } : x)) })} /></td>
                    <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setForm({ ...form, materials: form.materials.filter((_, j) => j !== i) })}><Icon d={I.x} size={14} /></button></td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </Card>
        </Modal>
      )}
    </>
  );
}
