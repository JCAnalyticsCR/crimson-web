/* Panel "Tiempo" de la orden de trabajo (Andres 30:48: "antes salía para ponerle el tiempo, ahora no me sale eso").
   Los botones grandes ya no dependen de ser el tecnico: los ve quien registra (tecnico, ayudantes) y quien asigna
   (supervisor, admin). El tiempo va por jornada y por persona: una instalacion de dos dias no cuenta la noche y dos
   tecnicos cuentan doble (persona-horas). Sin dinero en ningun lado: el tecnico no ve precios ni costos. */
import { useEffect, useState } from "react";
import { api, parseTs } from "../../lib/api";
import { Card, I, Icon } from "../../ui/components";

export type TimeEntry = {
  id: number; user_id: number; user: string | null; started_at: string; ended_at: string | null; minutes: number | null;
  kind: string; note: string | null; created_by: number | null; created_by_name: string | null;
};
export type Running = { id: number; user_id: number; user: string | null; started_at: string };
/** active: solo en el detalle; false = ya no puede recibir ordenes (inactivo o cambio de rol) */
export type Crew = { id: number; name: string | null; role: string; active?: boolean };
type OrderLike = {
  id: number; number: string; status: string; trashed_at?: string | null; technician_id: number | null; helpers: number[];
  hours: number | null; hours_source?: "registro" | "legado" | null; running?: Running[]; stale?: boolean; crew?: Crew[];
  hours_estimated?: number | null; hours_estimated_label?: string | null; time_entries?: TimeEntry[];
};

const CR_MS = 6 * 3600_000;
const crDate = (iso: string) => new Date(parseTs(iso).getTime() - CR_MS); // se lee con getUTC*: hora de Costa Rica
const hhmm = (iso: string | null) => { if (!iso) return "—"; const x = crDate(iso); const h = x.getUTCHours(); return `${h % 12 || 12}:${String(x.getUTCMinutes()).padStart(2, "0")} ${h < 12 ? "a. m." : "p. m."}`; };
const dia = (iso: string) => crDate(iso).toLocaleDateString("es-CR", { weekday: "short", day: "2-digit", month: "short", timeZone: "UTC" });
const fmtH = (h: number) => h.toLocaleString("es-CR", { maximumFractionDigits: 2 });
export const durMin = (m: number) => { const h = Math.floor(m / 60), r = Math.max(0, Math.round(m % 60)); return h ? `${h} h ${String(r).padStart(2, "0")} min` : `${r} min`; };
/** "En curso · 2 h 10" para las listas */
export const enCurso = (running: Running[] | undefined, now: number) => {
  if (!running?.length) return null;
  const desde = Math.min(...running.map((r) => parseTs(r.started_at).getTime()));
  const m = Math.max(0, Math.floor((now - desde) / 60_000));
  return `En curso · ${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
};
const toLocalParts = (iso: string | null) => { if (!iso) return { date: "", time: "" }; const s = crDate(iso).toISOString(); return { date: s.slice(0, 10), time: s.slice(11, 16) }; };
const fromParts = (date: string, time: string) => (date && time ? new Date(`${date}T${time}:00-06:00`).toISOString() : null);
const hoyCR = () => new Date(Date.now() - CR_MS).toISOString().slice(0, 10);

type Form = { id?: number; user_id: string; date: string; start: string; end: string; note: string };

export function WorkOrderTime({ order: o, meId, puedeRegistrar, puedeAsignar, busy, faltaAnotar, onAct, run }: {
  order: OrderLike; meId: number | undefined;
  /** field.editar y (del equipo o asigna), orden abierta y fuera de la papelera */
  puedeRegistrar: boolean;
  /** field.asignar: supervisor o admin (corrige el tiempo de cualquiera, tambien despues de finalizar) */
  puedeAsignar: boolean;
  busy: boolean;
  faltaAnotar: string[];
  onAct: (action: string, extra?: Record<string, unknown>) => void;
  /** llama a la API (tiempo) sin pisar lo que se esta escribiendo en el detalle */
  run: (fn: () => Promise<OrderLike>, ok: string) => Promise<boolean>;
}) {
  const [now, setNow] = useState(() => Date.now());
  const [quienes, setQuienes] = useState<number[] | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const running = o.running || [];
  const crew = o.crew || [];
  const abierta = !["finalizada", "cancelada"].includes(o.status) && !o.trashed_at;
  useEffect(() => {
    if (!running.length) return;
    const t = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(t);
  }, [running.length]);

  const puedeTocar = (e: TimeEntry) => puedeAsignar || (e.user_id === meId && abierta);
  const puedeAgregar = puedeAsignar || (puedeRegistrar && abierta);
  // por defecto alguien de la lista (el tecnico o, sin tecnico, el primer ayudante): si no, el select mostraria al primer
  // ayudante y se mandaria el tiempo a nombre de quien asigna
  const nuevo = (): Form => ({ user_id: String(puedeAsignar ? (o.technician_id ?? crew[0]?.id ?? meId ?? "") : meId ?? ""), date: hoyCR(), start: "08:00", end: "", note: "" });
  const editar = (e: TimeEntry) => { const a = toLocalParts(e.started_at), b = toLocalParts(e.ended_at); setForm({ id: e.id, user_id: String(e.user_id), date: a.date, start: a.time, end: b.date === a.date ? b.time : b.time, note: e.note || "" }); };

  const guardarTiempo = async () => {
    if (!form) return;
    const inicio = fromParts(form.date, form.start);
    let fin = fromParts(form.date, form.end);
    if (inicio && fin && fin <= inicio) fin = new Date(new Date(fin).getTime() + 86_400_000).toISOString(); // termino despues de medianoche
    if (!form.id && o.hours_source === "legado" && o.hours != null && !confirm(`Desde ahora las horas son la suma del registro (antes: ${fmtH(o.hours)} h). ¿Seguimos?`)) return;
    const body = { user_id: form.user_id ? Number(form.user_id) : null, started_at: inicio, ended_at: fin, note: form.note || null };
    const ok = await run(() => api<OrderLike>(form.id ? `/work-orders/${o.id}/time/${form.id}` : `/work-orders/${o.id}/time`, { method: form.id ? "PUT" : "POST", json: body }), form.id ? "Tiempo corregido" : "Tiempo agregado");
    if (ok) setForm(null);
  };
  const borrar = async (e: TimeEntry) => {
    if (!confirm(`¿Borrar el tiempo de ${e.user || "esta persona"} del ${dia(e.started_at)}?`)) return;
    await run(async () => { await api(`/work-orders/${o.id}/time/${e.id}`, { method: "DELETE" }); return api<OrderLike>(`/work-orders/${o.id}`); }, "Tiempo borrado");
  };

  const corriendoDesde = running.length ? Math.min(...running.map((r) => parseTs(r.started_at).getTime())) : null;
  const vieja = o.stale ? [...running].sort((a, b) => parseTs(a.started_at).getTime() - parseTs(b.started_at).getTime())[0] : null;
  const entradas = o.time_entries || [];
  const personas = puedeAsignar ? crew : crew.filter((c) => c.id === meId);
  /* El select siempre muestra lo que se manda: "Yo" cuando quien registra no esta en la lista (el supervisor tambien
     puede anotarse; queda como ayudante) y la persona de una entrada que ya salio del equipo. */
  const enLista = (id: string) => personas.some((c) => String(c.id) === id);
  const conYo = meId != null && !enLista(String(meId)) && (puedeAsignar || personas.length === 0);
  const ajena = form && form.user_id && !enLista(form.user_id) && !(conYo && form.user_id === String(meId)) ? form.user_id : null;

  return (
    <Card title={<span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}><Icon d={I.clock} size={18} />Tiempo</span>}
      extra={puedeAgregar && !form ? <button className="btn btn--soft btn--sm" onClick={() => setForm(nuevo())}><Icon d={I.plus} />Agregar tiempo</button> : undefined}>
      {o.hours_estimated ? <p style={{ margin: "0 0 4px", fontSize: 13.5 }}>Estimado del proyecto: <b>{fmtH(o.hours_estimated)} h·persona</b>{o.hours_estimated_label ? ` (${o.hours_estimated_label})` : ""}</p> : null}
      <p style={{ margin: "0 0 10px", fontSize: 13.5 }}>
        Registrado: <b>{o.hours != null ? `${fmtH(o.hours)} h·persona` : "—"}</b>
        {o.hours_source === "legado" && <span className="muted"> (calculado de inicio a fin)</span>}
      </p>
      {corriendoDesde !== null && (
        <p style={{ margin: "0 0 10px", fontSize: 13.5, color: "var(--warn)" }}>
          Corriendo: {running.map((r) => r.user || "—").join(", ")} desde {hhmm(new Date(corriendoDesde).toISOString())} ({durMin(Math.max(0, (now - corriendoDesde) / 60_000))})
        </p>
      )}
      {vieja && (
        <div role="alert" style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", border: "1px solid var(--warn)", background: "var(--warn-soft)", borderRadius: 10, padding: "8px 12px", fontSize: 13, marginBottom: 12 }}>
          <span style={{ flex: "1 1 200px" }}>Quedó corriendo desde {dia(vieja.started_at)} {hhmm(vieja.started_at)}: corregí la hora de salida.</span>
          {(puedeAsignar || vieja.user_id === meId) && <button className="btn btn--soft btn--sm" onClick={() => { const e = entradas.find((x) => x.id === vieja.id); if (e) editar(e); }}>Corregir</button>}
        </div>
      )}

      {puedeRegistrar && (
        <>
          <div className="bigbtn" style={{ marginBottom: 12, flexWrap: "wrap" }}>
            {o.status === "asignada" && <button className="btn btn--soft" onClick={() => onAct("arrive")} disabled={busy}><Icon d={I.check} />Llegué al sitio</button>}
            {/* preselecciona solo a quien hoy puede recibir ordenes (un ayudante dado de baja no abre jornada) */}
            {["asignada", "en_sitio", "en_proceso"].includes(o.status) && !running.length && quienes === null && (
              <button className="btn btn--soft" onClick={() => setQuienes(crew.filter((c) => c.active !== false).map((c) => c.id))} disabled={busy}><Icon d={I.clock} />Iniciar jornada</button>
            )}
            {running.length > 0 && <button className="btn btn--soft" onClick={() => onAct("pause")} disabled={busy}>Terminar jornada</button>}
            {o.status === "en_proceso" && (
              <button className="btn btn--crimson" onClick={() => { if (confirm("Al finalizar se descuenta de inventario el material usado. ¿Confirmás?")) onAct("finish"); }} disabled={busy || faltaAnotar.length > 0} title={faltaAnotar.length ? `Falta anotar: ${faltaAnotar.join(", ")}` : undefined}>Finalizar</button>
            )}
          </div>
          {quienes !== null && (
            <div style={{ border: "1px solid var(--hair-2)", borderRadius: 12, padding: 12, marginBottom: 12, display: "flex", flexDirection: "column", gap: 10 }}>
              <b style={{ fontSize: 13.5 }}>¿Quiénes trabajan hoy?</b>
              {crew.length === 0 ? <p className="muted" style={{ margin: 0, fontSize: 13 }}>La orden no tiene técnico: asignalo arriba antes de iniciar.</p> : (
                <div className="chips">
                  {crew.map((c) => {
                    const baja = c.active === false;
                    const on = !baja && quienes.includes(c.id);
                    return <button type="button" key={c.id} className={`chip${on ? " is-on" : ""}`} aria-pressed={on} disabled={baja} title={baja ? "Ya no puede recibir órdenes: quitalo de la asignación" : undefined} onClick={() => setQuienes(on ? quienes.filter((x) => x !== c.id) : [...quienes, c.id])}>{c.name || c.id}{c.role === "tecnico" ? " · técnico" : ""}{baja ? " · inactivo" : ""}</button>;
                  })}
                </div>
              )}
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
                <button className="btn btn--ghost btn--sm" onClick={() => setQuienes(null)}>Cancelar</button>
                <button className="btn btn--crimson btn--sm" disabled={busy || quienes.length === 0} onClick={() => { onAct("start", { workers: quienes }); setQuienes(null); }}>Iniciar jornada</button>
              </div>
            </div>
          )}
        </>
      )}

      {form && (
        <div style={{ border: "1px solid var(--hair-2)", borderRadius: 12, padding: 12, marginBottom: 12, display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", alignItems: "end" }}>
          <label className="field" style={{ margin: 0 }}><span className="meta">Persona</span>
            <select className="select" value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value })} disabled={!puedeAsignar}>
              {personas.map((c) => <option key={c.id} value={c.id}>{c.name || c.id}{c.active === false ? " (inactivo)" : ""}</option>)}
              {conYo && <option value={meId}>Yo</option>}
              {ajena && <option value={ajena}>{entradas.find((x) => String(x.user_id) === ajena)?.user || "Otra persona"}</option>}
            </select>
          </label>
          <label className="field" style={{ margin: 0 }}><span className="meta">Fecha</span><input className="input" type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></label>
          <label className="field" style={{ margin: 0 }}><span className="meta">Inicio</span><input className="input" type="time" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} /></label>
          <label className="field" style={{ margin: 0 }}><span className="meta">Fin</span><input className="input" type="time" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} /></label>
          <label className="field" style={{ margin: 0, gridColumn: "1 / -1" }}><span className="meta">Nota</span><input className="input" maxLength={200} value={form.note} placeholder="Olvidó iniciar, se quedó a terminar…" onChange={(e) => setForm({ ...form, note: e.target.value })} /></label>
          <div style={{ gridColumn: "1 / -1", display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
            <button className="btn btn--ghost btn--sm" onClick={() => setForm(null)}>Cancelar</button>
            <button className="btn btn--crimson btn--sm" disabled={busy || !form.date || !form.start || (!form.end && !form.id)} onClick={guardarTiempo}>{form.id ? "Guardar corrección" : "Agregar"}</button>
          </div>
        </div>
      )}

      {entradas.length === 0 ? <p className="muted" style={{ margin: 0, fontSize: 13 }}>Sin jornadas registradas todavía.</p> : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
          {entradas.map((e) => (
            <li key={e.id} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", fontSize: 13, borderBottom: "1px solid var(--hair)", paddingBottom: 8 }}>
              <span className="mono muted" style={{ minWidth: 92 }}>{dia(e.started_at)}</span>
              <b style={{ flex: "1 1 120px" }}>{e.user || "—"}</b>
              <span className="mono">{hhmm(e.started_at)} – {e.ended_at ? hhmm(e.ended_at) : "corriendo"}</span>
              <span className="mono">{e.minutes != null ? durMin(e.minutes) : "—"}</span>
              <span className={`badge badge--${e.kind === "manual" ? "info" : "muted"}`}>{e.kind === "manual" ? `manual por ${e.created_by_name || "—"}` : "reloj"}</span>
              {e.note && <span className="muted" style={{ flexBasis: "100%" }}>{e.note}</span>}
              {puedeTocar(e) && (
                <span style={{ display: "flex", gap: 4, marginLeft: "auto" }}>
                  <button className="btn btn--ghost btn--sm" onClick={() => editar(e)}>Editar</button>
                  <button className="btn btn--ghost btn--sm" aria-label="Borrar" onClick={() => borrar(e)}><Icon d={I.x} size={13} /></button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
