/* Ventas etapa 3 · agenda del vendedor: llamadas, visitas y recordatorios ligados a un cliente, una oportunidad o una
   cotización. La hora se escribe y se muestra en hora de Costa Rica (la API la guarda en UTC). La lista «Hoy» del Inicio
   trae lo de hoy y lo atrasado (resaltado). El vendedor solo ve y crea lo suyo; admin y jefe asignan a cualquiera. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, Modal } from "../../ui/components";

export type Task = {
  id: number; kind: string; kind_label: string; title: string; notes: string | null; due_at: string; done_at: string | null; overdue: boolean; today: boolean;
  assigned_to: number; assigned_name: string | null; customer: { id: number; name: string } | null; opportunity: { id: number; number: string; title: string } | null; quote: { id: number; number: string } | null;
};

const CR = "America/Costa_Rica";
export const crWhen = (s: string, withDate = true) =>
  new Date(s).toLocaleString("es-CR", { timeZone: CR, ...(withDate ? { day: "2-digit", month: "short" } : {}), hour: "2-digit", minute: "2-digit" });
const nowLocal = () => {
  const p = new Intl.DateTimeFormat("en-CA", { timeZone: CR, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date());
  const g = (t: string) => p.find((x) => x.type === t)?.value || "00";
  return `${g("year")}-${g("month")}-${g("day")}T${g("hour")}:${g("minute")}`;
};

export function TaskModal({ link, onClose, onDone }: { link?: { customer_id?: number; opportunity_id?: number; quote_id?: number; label?: string }; onClose: () => void; onDone?: () => void }) {
  const { toast, allows } = useSession();
  const [f, setF] = useState({ kind: "llamada", title: "", notes: "", due_at: nowLocal(), assigned_to: "" });
  const [team, setTeam] = useState<{ id: number; name: string }[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (allows("crm.cartera")) api<{ id: number; name: string }[]>("/cartera/sellers").then(setTeam).catch(() => setTeam([])); }, [allows]);
  const save = async () => {
    setBusy(true);
    try {
      await api("/tasks", { method: "POST", json: { kind: f.kind, title: f.title.trim(), notes: f.notes.trim() || null, due_at: f.due_at, assigned_to: f.assigned_to ? Number(f.assigned_to) : null, ...(link || {}), label: undefined } });
      toast("Tarea agendada"); onDone?.(); onClose();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } finally { setBusy(false); }
  };
  return (
    <Modal title="Nueva tarea" onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" onClick={save} disabled={busy || f.title.trim().length < 2 || !f.due_at}>{busy ? "Guardando…" : "Agendar"}</button></>}>
      {link?.label && <p className="muted" style={{ fontSize: 13, margin: 0 }}>Para: <b>{link.label}</b></p>}
      <div className="grid-2">
        <Field label="Tipo"><select className="select" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="llamada">Llamada</option><option value="visita">Visita</option><option value="recordatorio">Recordatorio</option></select></Field>
        <Field label="Fecha y hora (Costa Rica)"><input className="input" type="datetime-local" value={f.due_at} onChange={(e) => setF({ ...f, due_at: e.target.value })} /></Field>
      </div>
      <Field label="Qué hay que hacer"><input className="input" maxLength={200} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="Por ejemplo: llamar para cerrar la propuesta" /></Field>
      <Field label="Notas (opcional)"><textarea className="textarea" maxLength={2000} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      {team.length > 0 && (
        <Field label="Para quién"><select className="select" value={f.assigned_to} onChange={(e) => setF({ ...f, assigned_to: e.target.value })}><option value="">Para mí</option>{team.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></Field>
      )}
    </Modal>
  );
}

export function TaskRow({ t, onChanged }: { t: Task; onChanged: () => void }) {
  const { toast } = useSession();
  const toggle = async () => {
    try { await api(`/tasks/${t.id}`, { method: "PATCH", json: { done: !t.done_at } }); onChanged(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const ref = t.quote ? <Link to={`/cotizaciones/${t.quote.id}`}>{t.quote.number}</Link> : t.opportunity ? <Link to={`/oportunidades?id=${t.opportunity.id}`}>{t.opportunity.number}</Link> : null;
  return (
    <li style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 14px", borderBottom: "1px solid var(--hair)", background: t.overdue ? "color-mix(in srgb, var(--bad) 8%, transparent)" : undefined }}>
      <input type="checkbox" checked={!!t.done_at} onChange={toggle} aria-label={`Marcar «${t.title}» como hecha`} style={{ marginTop: 4 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, textDecoration: t.done_at ? "line-through" : undefined }}><b>{t.kind_label}</b> · {t.title}</div>
        <div className="meta" style={{ textTransform: "none", display: "flex", gap: 8, flexWrap: "wrap" }}>
          <span style={t.overdue ? { color: "var(--bad)", fontWeight: 600 } : undefined}>{t.overdue ? "Atrasada · " : ""}{crWhen(t.due_at, !t.today)}</span>
          {t.customer && <Link to={`/clientes/${t.customer.id}`}>{t.customer.name}</Link>}
          {ref}
        </div>
      </div>
    </li>
  );
}

export function TodayCard({ tasks, onChanged }: { tasks: Task[]; onChanged: () => void }) {
  const [nueva, setNueva] = useState(false);
  const atrasadas = tasks.filter((t) => t.overdue).length;
  return (
    <Card title={`Hoy · ${tasks.length}${atrasadas ? ` (${atrasadas} atrasada${atrasadas > 1 ? "s" : ""})` : ""}`} flush extra={<button className="btn btn--soft btn--sm" onClick={() => setNueva(true)}>Nueva tarea</button>}>
      {tasks.length === 0 ? <Empty title="Nada pendiente para hoy" hint="Agende llamadas, visitas y recordatorios desde aquí, la ficha del cliente o la cotización." /> : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>{tasks.map((t) => <TaskRow key={t.id} t={t} onChanged={onChanged} />)}</ul>
      )}
      {nueva && <TaskModal onClose={() => setNueva(false)} onDone={onChanged} />}
    </Card>
  );
}
