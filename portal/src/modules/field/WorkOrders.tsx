/* Ordenes de trabajo. Dos pantallas en una:
   - El tecnico ve "mi dia" y trabaja con cuatro botones grandes: llegue, iniciar, avanzar, finalizar.
   - El supervisor asigna, programa y revisa. Al finalizar, el material usado sale de inventario una sola vez. */
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import { Lookup, searchCustomers, searchProducts } from "../../ui/Lookup";

type Material = { id?: number; product_id: number | null; name: string; quantity: string; unit: string; planned: string };
type Task = { text: string; done?: boolean };
export type Order = {
  id: number; number: string; title: string; kind: string; status: string; site: string | null; scheduled_at: string | null;
  customer_id: number | null; customer: string | null; project_id: number | null; project: string | null;
  technician_id: number | null; technician: string | null; helpers: number[]; arrived_at: string | null; started_at: string | null;
  finished_at: string | null; tasks: Task[]; photos: string[]; notes: string | null; customer_signature: string | null;
  stock_applied: boolean; hours: number | null; materials?: Material[];
};
type Today = { today: Order[]; next: Order[]; surveys: { id: number; number: string; kind_label: string; site: string | null }[] };
type Tech = { id: number; name: string; role: string };

const STATUS: Record<string, { label: string; tone: string }> = {
  asignada: { label: "Asignada", tone: "info" }, en_sitio: { label: "En sitio", tone: "warn" }, en_proceso: { label: "En proceso", tone: "warn" },
  finalizada: { label: "Finalizada", tone: "ok" }, cancelada: { label: "Cancelada", tone: "muted" },
};
const KINDS: Record<string, string> = { instalacion: "Instalación", visita: "Visita", mantenimiento: "Mantenimiento", soporte: "Soporte" };

/* Costa Rica es UTC-6 todo el año (no hay horario de verano). La agenda se escribe y se lee en hora de aquí,
   aunque la computadora del usuario tenga mal la zona: si no, un trabajo de las 9 a. m. aparece a las 3 p. m. */
const CR_OFFSET = "-06:00";
const toISO = (local: string) => (local ? new Date(`${local}:00${CR_OFFSET}`).toISOString() : null);
const toLocalInput = (iso: string | null) => (iso ? new Date(parseTs(iso).getTime() - 6 * 3600_000).toISOString().slice(0, 16) : "");
const hhmm = (s: string | null) => (s ? parseTs(s).toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit", timeZone: "America/Costa_Rica" }) : "—");
const dayLabel = (s: string | null) => (s ? parseTs(s).toLocaleDateString("es-CR", { weekday: "short", day: "2-digit", month: "short", timeZone: "America/Costa_Rica" }) : "Sin fecha");
const blank = { title: "", kind: "instalacion", project_id: "", customer_id: "", customer_name: "", site: "", scheduled_at: "", technician_id: "", notes: "", tasks: [] as Task[], materials: [] as Material[] };

export default function WorkOrders() {
  const { toast, allows, me } = useSession();
  const [params, setParams] = useSearchParams();
  const [day, setDay] = useState<Today | null>(null);
  const [all, setAll] = useState<Order[]>([]);
  const [tab, setTab] = useState<"dia" | "todas">("dia");
  const [open, setOpen] = useState<Order | null>(null);
  const [form, setForm] = useState<(typeof blank & { id?: number }) | null>(null);
  const [techs, setTechs] = useState<Tech[]>([]);
  const [busy, setBusy] = useState(false);
  const puedeAsignar = allows("field.asignar");

  const load = useCallback(() => {
    api<Today>("/work-orders/meta/today").then(setDay).catch(() => setDay(null));
    api<Order[]>("/work-orders?limit=200").then(setAll).catch(() => setAll([]));
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    api<Tech[]>("/field/technicians").then(setTechs).catch(() => setTechs([]));
  }, []);

  const show = useCallback(async (id: number) => setOpen(await api<Order>(`/work-orders/${id}`)), []);
  useEffect(() => { const id = params.get("id"); if (id) show(Number(id)); }, [params, show]);

  const act = async (action: string, extra: Record<string, unknown> = {}) => {
    if (!open) return;
    setBusy(true);
    try {
      const o = await api<Order>(`/work-orders/${open.id}/${action}`, { method: "POST", json: { tasks: open.tasks, photos: open.photos, notes: open.notes, materials: (open.materials || []).map((m) => ({ id: m.id, product_id: m.product_id, name: m.name, quantity: Number(m.quantity || 0), unit: m.unit, planned: Number(m.planned || 0) })), ...extra } });
      setOpen(o);
      load();
      toast(action === "finish" ? "Orden finalizada; el material usado salió de inventario." : "Guardado");
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  const setMat = (i: number, patch: Partial<Material>) => setOpen((o) => (o ? { ...o, materials: (o.materials || []).map((m, j) => (j === i ? { ...m, ...patch } : m)) } : o));
  const setTask = (i: number, done: boolean) => setOpen((o) => (o ? { ...o, tasks: o.tasks.map((t, j) => (j === i ? { ...t, done } : t)) } : o));

  const save = async () => {
    if (!form) return;
    const body = {
      title: form.title, kind: form.kind, project_id: form.project_id ? Number(form.project_id) : null, customer_id: form.customer_id ? Number(form.customer_id) : null,
      site: form.site || null, scheduled_at: toISO(form.scheduled_at),
      technician_id: form.technician_id ? Number(form.technician_id) : null, helpers: [], tasks: form.tasks, notes: form.notes || null,
      materials: form.materials.filter((m) => m.name.trim()).map((m) => ({ id: m.id, product_id: m.product_id, name: m.name, quantity: Number(m.quantity || 0), unit: m.unit || "Unid", planned: Number(m.planned || 0) })),
    };
    try {
      await api(form.id ? `/work-orders/${form.id}` : "/work-orders", { method: form.id ? "PUT" : "POST", json: body });
      toast("Orden guardada"); setForm(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const jobCard = (o: Order) => (
    <button className="job" key={o.id} onClick={() => show(o.id)}>
      <span className="job__when">{o.scheduled_at ? hhmm(o.scheduled_at) : "—"}<br />{dayLabel(o.scheduled_at).split(",")[0]}</span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <b>{o.title}</b>
        <small>{o.customer || "Sin cliente"}{o.site ? ` · ${o.site}` : ""}</small>
      </span>
      <span className={`badge badge--${STATUS[o.status]?.tone || "muted"}`}>{STATUS[o.status]?.label || o.status}</span>
    </button>
  );

  const soyElTecnico = !!open && (open.technician_id === me?.user.id || !puedeAsignar);

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
          {all.length === 0 ? <Empty hint="Cada visita e instalación se programa como orden de trabajo y deja su evidencia." /> : (
            <table className="table">
              <thead><tr><th>Número</th><th>Trabajo</th><th>Cliente</th><th>Proyecto</th><th>Técnico</th><th>Programada</th><th>Estado</th><th /></tr></thead>
              <tbody>{all.map((o) => (
                <tr key={o.id}>
                  <td className="mono muted">{o.number}</td><td style={{ fontWeight: 600 }}>{o.title}</td><td className="muted">{o.customer || "—"}</td>
                  <td className="mono muted">{o.project || "—"}</td><td className="muted">{o.technician || "Sin asignar"}</td>
                  <td style={{ fontSize: 13 }}>{o.scheduled_at ? `${dayLabel(o.scheduled_at)} ${hhmm(o.scheduled_at)}` : "—"}</td>
                  <td><span className={`badge badge--${STATUS[o.status]?.tone || "muted"}`}>{STATUS[o.status]?.label || o.status}</span></td>
                  <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => show(o.id)}>Abrir</button></td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </Card>
      )}

      {open && (
        <Modal title={`${open.number} · ${open.title}`} onClose={() => { setOpen(null); if (params.get("id")) setParams({}); }} wide foot={<>
          {puedeAsignar && open.status !== "finalizada" && <button className="btn btn--ghost" style={{ marginRight: "auto" }} onClick={() => { setForm({ id: open.id, title: open.title, kind: open.kind, project_id: open.project_id ? String(open.project_id) : "", customer_id: open.customer_id ? String(open.customer_id) : "", customer_name: open.customer || "", site: open.site || "", scheduled_at: toLocalInput(open.scheduled_at), technician_id: open.technician_id ? String(open.technician_id) : "", notes: open.notes || "", tasks: open.tasks || [], materials: (open.materials || []).map((m) => ({ ...m, quantity: String(m.quantity), planned: String(m.planned) })) }); setOpen(null); }}>Editar</button>}
          <button className="btn btn--ghost" onClick={() => setOpen(null)}>Cerrar</button>
        </>}>
          <div className="eco" style={{ marginBottom: 14 }}>
            <div className="eco__box"><span className="meta">Estado</span><b style={{ fontSize: 15 }}>{STATUS[open.status]?.label || open.status}</b></div>
            <div className="eco__box"><span className="meta">Llegada</span><b style={{ fontSize: 15 }}>{hhmm(open.arrived_at)}</b></div>
            <div className="eco__box"><span className="meta">Inicio</span><b style={{ fontSize: 15 }}>{hhmm(open.started_at)}</b></div>
            <div className="eco__box"><span className="meta">Horas</span><b>{open.hours ?? "—"}</b></div>
          </div>
          <p className="muted" style={{ fontSize: 13.5, marginTop: 0 }}>
            {KINDS[open.kind] || open.kind} · {open.customer || "Sin cliente"}{open.site ? ` · ${open.site}` : ""}
            {open.project && <> · <Link to={`/proyectos/${open.project_id}`}>{open.project}</Link></>}
          </p>

          {soyElTecnico && open.status !== "finalizada" && open.status !== "cancelada" && (
            <div className="bigbtn" style={{ marginBottom: 16 }}>
              {open.status === "asignada" && <button className="btn btn--soft" onClick={() => act("arrive")} disabled={busy}><Icon d={I.check} />Llegué al sitio</button>}
              {open.status === "en_sitio" && <button className="btn btn--soft" onClick={() => act("start")} disabled={busy}><Icon d={I.check} />Iniciar trabajo</button>}
              {(open.status === "en_proceso" || open.status === "en_sitio") && <button className="btn btn--ghost" onClick={() => act("progress")} disabled={busy}>Guardar avance</button>}
              {open.status === "en_proceso" && <button className="btn btn--crimson" onClick={() => { if (confirm("Al finalizar se descuenta de inventario el material usado. ¿Confirmás?")) act("finish"); }} disabled={busy}>Finalizar</button>}
            </div>
          )}

          {open.tasks?.length > 0 && (
            <Card title="Tareas">
              {open.tasks.map((t, i) => (
                <label className={`task${t.done ? " is-done" : ""}`} key={i}>
                  <input type="checkbox" checked={!!t.done} disabled={open.status === "finalizada"} onChange={(e) => setTask(i, e.target.checked)} />
                  <span>{t.text}</span>
                </label>
              ))}
            </Card>
          )}

          <Card title="Material usado" flush>
            {(open.materials || []).length === 0 ? <p className="muted" style={{ fontSize: 13, padding: "0 18px 14px" }}>Sin materiales planificados.</p> : (
              <table className="table">
                <thead><tr><th>Material</th><th className="num">Planificado</th><th className="num">Usado</th></tr></thead>
                <tbody>{(open.materials || []).map((m, i) => (
                  <tr key={m.id ?? i}>
                    <td style={{ fontWeight: 600 }}>{m.name}</td>
                    <td className="num mono muted">{Number(m.planned)} {m.unit}</td>
                    <td className="num">{open.stock_applied ? <span className="mono">{Number(m.quantity)}</span> : <input className="input input--mono" style={{ maxWidth: 110 }} inputMode="decimal" value={m.quantity} onChange={(e) => setMat(i, { quantity: e.target.value })} />}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </Card>

          <Field label="Notas del trabajo"><textarea className="textarea" value={open.notes || ""} disabled={open.status === "finalizada"} onChange={(e) => setOpen({ ...open, notes: e.target.value })} placeholder="Qué se hizo, qué quedó pendiente, qué encontró el técnico." /></Field>
          <Field label="Recibido por (nombre del cliente)" hint="Queda en el informe de entrega.">
            <input className="input" value={open.customer_signature || ""} disabled={open.status === "finalizada"} onChange={(e) => setOpen({ ...open, customer_signature: e.target.value })} />
          </Field>
          {open.stock_applied && <p className="muted" style={{ fontSize: 12.5 }}>El inventario de esta orden ya se descontó; por eso no se puede reescribir.</p>}
        </Modal>
      )}

      {form && (
        <Modal title={form.id ? "Editar orden" : "Asignar orden de trabajo"} onClose={() => setForm(null)} wide foot={<>
          <button className="btn btn--ghost" onClick={() => setForm(null)}>Cancelar</button>
          <button className="btn btn--crimson" onClick={save} disabled={form.title.trim().length < 2}>Guardar</button>
        </>}>
          <div className="grid-3">
            <Field label="Trabajo"><input className="input" autoFocus value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Instalación de 8 cámaras" /></Field>
            <Field label="Tipo"><select className="select" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{Object.entries(KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            <Field label="Técnico"><select className="select" value={form.technician_id} onChange={(e) => setForm({ ...form, technician_id: e.target.value })}><option value="">Sin asignar</option>{techs.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field>
            <Field label="Cliente"><Lookup value={form.customer_name} placeholder="Buscar cliente…" fetcher={searchCustomers} onSelect={(it, text) => setForm({ ...form, customer_id: it ? String(it.id) : "", customer_name: text })} /></Field>
            <Field label="Fecha y hora"><input className="input" type="datetime-local" value={form.scheduled_at} onChange={(e) => setForm({ ...form, scheduled_at: e.target.value })} /></Field>
            <Field label="Sitio"><input className="input" value={form.site} onChange={(e) => setForm({ ...form, site: e.target.value })} /></Field>
          </div>
          <Field label="Tareas" hint="Una por línea; el técnico las va marcando.">
            <textarea className="textarea" value={form.tasks.map((t) => t.text).join("\n")} onChange={(e) => setForm({ ...form, tasks: e.target.value.split("\n").filter((x) => x.trim()).map((x) => ({ text: x.trim(), done: false })) })} placeholder={"Montar cámaras\nCorrer cable\nConfigurar grabador"} />
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
