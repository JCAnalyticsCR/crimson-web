/* Mesa de soporte. Es la pieza que Andres necesita para la propuesta a Hikvision: cada caso con su
   reloj de primera respuesta, su nivel (1 a 3) y todo lo que se hizo escrito con hora y autor. */
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtMoney, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import { Lookup, searchCustomers } from "../../ui/Lookup";
import { PhotoStrip } from "../../ui/MediaPicker";

type Note = { id: number; body: string; internal: boolean; photos: string[]; user: string; created_at: string };
type Ticket = {
  id: number; number: string; subject: string; kind: string; channel: string | null; level: number; priority: string; status: string;
  customer_id: number | null; customer: string | null; asset_id: number | null; asset: string | null; project_id: number | null;
  work_order_id: number | null; assigned_to: number | null; assigned: string | null; due_at: string | null; first_reply_at: string | null;
  resolved_at: string | null; sla_vencido: boolean; hours: string; billable: boolean; amount: string; tags: string[]; created_at: string;
  description?: string | null; solution?: string | null; photos?: string[]; notes?: Note[];
};
type Meta = { kinds: string[]; states: string[]; priorities: string[]; channels: string[]; sla_horas: Record<string, number>; agents: { id: number; name: string }[]; abiertos: number; can_see_all: boolean };

const KIND: Record<string, string> = { soporte: "Soporte", garantia: "Garantía", mantenimiento: "Mantenimiento", visita: "Visita", instalacion: "Instalación", consulta: "Consulta" };
const ESTADO: Record<string, { label: string; tone: string }> = {
  nuevo: { label: "Nuevo", tone: "info" }, asignado: { label: "Asignado", tone: "info" }, en_proceso: { label: "En proceso", tone: "warn" },
  esperando_cliente: { label: "Esperando al cliente", tone: "muted" }, resuelto: { label: "Resuelto", tone: "ok" }, cerrado: { label: "Cerrado", tone: "muted" },
};
const PRIO: Record<string, string> = { baja: "var(--text-3)", media: "var(--info)", alta: "var(--warn)", critica: "var(--bad)" };
const blank = { subject: "", customer_id: "", customer_name: "", kind: "soporte", channel: "whatsapp", level: 1, priority: "media", description: "", assigned_to: "", billable: false };

const cuando = (s: string | null) => (s ? parseTs(s).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "America/Costa_Rica" }) : "—");

export default function Tickets() {
  const { toast, allows, me } = useSession();
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<Ticket[]>([]);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [status, setStatus] = useState("abiertos");
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Ticket | null>(null);
  const [form, setForm] = useState<(typeof blank & { id?: number }) | null>(null);
  const [nota, setNota] = useState({ body: "", internal: false, hours: "", photos: [] as string[] });
  const [cerrar, setCerrar] = useState<{ solution: string; hours: string; billable: boolean; amount: string } | null>(null);
  const [visita, setVisita] = useState<{ technician_id: string; scheduled_at: string; site: string } | null>(null);

  const cliente = params.get("cliente");
  const load = useCallback(() => {
    const qs = new URLSearchParams();
    if (cliente) { qs.set("customer_id", cliente); qs.set("status", ""); }
    if (status && !cliente) qs.set("status", status);
    if (kind) qs.set("kind", kind);
    if (q) qs.set("q", q);
    api<Ticket[]>(`/tickets?${qs}`).then(setRows);
  }, [status, kind, q, cliente]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);
  useEffect(() => { api<Meta>("/tickets/meta/config").then(setMeta); }, []);

  const show = useCallback(async (id: number) => { setOpen(await api<Ticket>(`/tickets/${id}`)); setNota({ body: "", internal: false, hours: "", photos: [] }); }, []);
  useEffect(() => { const id = params.get("id"); if (id) show(Number(id)); }, [params, show]);

  const body = (f: typeof blank) => ({
    subject: f.subject, customer_id: f.customer_id ? Number(f.customer_id) : null, kind: f.kind, channel: f.channel || null,
    level: Number(f.level), priority: f.priority, description: f.description || null,
    assigned_to: f.assigned_to ? Number(f.assigned_to) : null, billable: f.billable, contact: {}, tags: [],
  });

  const save = async () => {
    if (!form) return;
    try {
      const t = await api<Ticket>(form.id ? `/tickets/${form.id}` : "/tickets", { method: form.id ? "PUT" : "POST", json: body(form) });
      toast(form.id ? "Ticket actualizado" : `Ticket ${t.number} abierto`);
      setForm(null); load(); if (form.id) show(form.id);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const responder = async () => {
    if (!open || nota.body.trim().length < 1) return;
    try {
      const t = await api<Ticket>(`/tickets/${open.id}/notes`, { method: "POST", json: { body: nota.body.trim(), internal: nota.internal, photos: nota.photos, hours: nota.hours ? Number(nota.hours) : null, status: open.status === "nuevo" || open.status === "asignado" ? "en_proceso" : null } });
      setOpen(t); setNota({ body: "", internal: false, hours: "", photos: [] }); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const mover = async (st: string, extra: Record<string, unknown> = {}) => {
    if (!open) return;
    try { const t = await api<Ticket>(`/tickets/${open.id}`, { method: "PATCH", json: { status: st, ...extra } }); setOpen(t); setCerrar(null); load(); toast("Ticket actualizado"); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const mandarTecnico = async () => {
    if (!open || !visita) return;
    try {
      const r = await api<{ number: string }>(`/tickets/${open.id}/work-order`, { method: "POST", json: { technician_id: visita.technician_id ? Number(visita.technician_id) : null, scheduled_at: visita.scheduled_at ? new Date(`${visita.scheduled_at}:00-06:00`).toISOString() : null, site: visita.site || null } });
      toast(`Orden ${r.number} creada`); setVisita(null); show(open.id);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const vencidos = rows.filter((t) => t.sla_vencido).length;

  return (
    <>
      <div className="page-head">
        <div><div className="meta">12 · Soporte</div><h1 className="h1">Tickets</h1></div>
        <div className="page-head__actions">
          {allows("support_desk.crear") && <button className="btn btn--crimson" onClick={() => setForm({ ...blank })}><Icon d={I.plus} />Nuevo ticket</button>}
        </div>
      </div>

      <div className="eco">
        <div className="eco__box"><span className="meta">Abiertos</span><b>{rows.filter((t) => !["resuelto", "cerrado"].includes(t.status)).length}</b></div>
        <div className={`eco__box ${vencidos ? "is-bad" : "is-good"}`}><span className="meta">Sin primera respuesta a tiempo</span><b>{vencidos}</b></div>
        <div className="eco__box"><span className="meta">Críticos y altos</span><b>{rows.filter((t) => ["critica", "alta"].includes(t.priority) && !["resuelto", "cerrado"].includes(t.status)).length}</b></div>
        <div className="eco__box"><span className="meta">Horas registradas</span><b>{rows.reduce((s, t) => s + Number(t.hours || 0), 0).toFixed(1)}</b></div>
      </div>

      <Card flush>
        <div className="list-head" style={{ flexWrap: "wrap" }}>
          <div className="search" style={{ maxWidth: 320 }}><Icon d={I.search} size={16} /><input placeholder="Asunto o número…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <select className="select select--sm" value={status} onChange={(e) => setStatus(e.target.value)}><option value="abiertos">Abiertos</option><option value="">Todos</option>{(meta?.states || []).map((s) => <option key={s} value={s}>{ESTADO[s]?.label || s}</option>)}</select>
          <select className="select select--sm" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">Todo tipo</option>{(meta?.kinds || []).map((k) => <option key={k} value={k}>{KIND[k] || k}</option>)}</select>
          <button className="btn btn--ghost btn--sm" onClick={load} style={{ marginLeft: "auto" }}><Icon d={I.refresh} /></button>
        </div>
        {rows.length === 0 ? <Empty title="Sin tickets" hint="Cada llamada, garantía o mantenimiento queda aquí con su tiempo de respuesta." /> : (
          <table className="table">
            <thead><tr><th>Número</th><th>Asunto</th><th>Cliente</th><th>Tipo</th><th>Prioridad</th><th>Responsable</th><th>Vence</th><th>Estado</th><th /></tr></thead>
            <tbody>{rows.map((t) => (
              <tr key={t.id}>
                <td className="mono muted">{t.number}</td>
                <td style={{ fontWeight: 600 }}>{t.subject}{t.level > 1 && <span className="badge badge--muted" style={{ marginLeft: 6 }}>N{t.level}</span>}</td>
                <td className="muted">{t.customer || "—"}</td><td>{KIND[t.kind] || t.kind}</td>
                <td><span style={{ color: PRIO[t.priority], fontWeight: 600, textTransform: "capitalize" }}>{t.priority}</span></td>
                <td className="muted">{t.assigned || "Sin asignar"}</td>
                <td style={{ fontSize: 13, color: t.sla_vencido ? "var(--bad)" : undefined }}>{t.first_reply_at ? "respondido" : cuando(t.due_at)}</td>
                <td><span className={`badge badge--${ESTADO[t.status]?.tone || "muted"}`}>{ESTADO[t.status]?.label || t.status}</span></td>
                <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => show(t.id)}>Abrir</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>

      {open && (
        <Modal title={`${open.number} · ${open.subject}`} onClose={() => { setOpen(null); if (params.get("id")) setParams({}); }} wide foot={<>
          {allows("field.asignar") && !open.work_order_id && <button className="btn btn--soft" style={{ marginRight: "auto" }} onClick={() => setVisita({ technician_id: "", scheduled_at: "", site: "" })}><Icon d={I.check} />Mandar un técnico</button>}
          {open.work_order_id && <Link className="btn btn--ghost" style={{ marginRight: "auto" }} to={`/ordenes-trabajo?id=${open.work_order_id}`}>Ver la orden</Link>}
          {allows("support_desk.editar") && !["resuelto", "cerrado"].includes(open.status) && <button className="btn btn--crimson" onClick={() => setCerrar({ solution: open.solution || "", hours: String(open.hours || ""), billable: open.billable, amount: String(open.amount || "") })}>Resolver</button>}
          <button className="btn btn--ghost" onClick={() => setOpen(null)}>Cerrar</button>
        </>}>
          <div className="eco" style={{ marginBottom: 14 }}>
            <div className="eco__box"><span className="meta">Estado</span><b style={{ fontSize: 15 }}>{ESTADO[open.status]?.label || open.status}</b></div>
            <div className={`eco__box ${open.sla_vencido ? "is-bad" : ""}`}><span className="meta">Primera respuesta</span><b style={{ fontSize: 15 }}>{open.first_reply_at ? cuando(open.first_reply_at) : `antes de ${cuando(open.due_at)}`}</b></div>
            <div className="eco__box"><span className="meta">Horas</span><b>{Number(open.hours || 0)}</b></div>
            <div className="eco__box"><span className="meta">{open.billable ? "Se cobra" : "Sin costo"}</span><b className="money">{open.billable ? fmtMoney(open.amount) : "—"}</b></div>
          </div>
          <p className="muted" style={{ fontSize: 13.5, marginTop: 0 }}>
            {KIND[open.kind] || open.kind} · nivel {open.level} · {open.channel || "sin canal"} · {open.customer || "sin cliente"}
            {open.asset ? ` · equipo: ${open.asset}` : ""} · {open.assigned || "sin responsable"}
            {allows("support_desk.editar") && <button className="btn btn--ghost btn--sm" style={{ marginLeft: 8 }} onClick={() => setForm({ id: open.id, subject: open.subject, customer_id: open.customer_id ? String(open.customer_id) : "", customer_name: open.customer || "", kind: open.kind, channel: open.channel || "", level: open.level, priority: open.priority, description: open.description || "", assigned_to: open.assigned_to ? String(open.assigned_to) : "", billable: open.billable })}>Editar</button>}
          </p>
          {open.description && <p style={{ fontSize: 14, background: "var(--bg-2)", padding: "10px 14px", borderRadius: 10 }}>{open.description}</p>}
          {open.solution && <p style={{ fontSize: 14, background: "var(--ok-soft, rgba(14,159,110,.08))", border: "1px solid var(--ok)", padding: "10px 14px", borderRadius: 10 }}><b>Solución:</b> {open.solution}</p>}

          <Card title={`Seguimiento · ${open.notes?.length || 0}`}>
            {(open.notes || []).map((n) => (
              <div key={n.id} style={{ borderLeft: `3px solid ${n.internal ? "var(--hair-2)" : "var(--crimson)"}`, paddingLeft: 12, marginBottom: 14 }}>
                <div className="meta">{n.user} · {cuando(n.created_at)}{n.internal ? " · nota interna" : ""}</div>
                <p style={{ margin: "4px 0", fontSize: 14, whiteSpace: "pre-wrap" }}>{n.body}</p>
                {!!n.photos?.length && <PhotoStrip value={n.photos} onChange={() => {}} disabled />}
              </div>
            ))}
            {allows("support_desk.editar") && !["cerrado"].includes(open.status) && (
              <>
                <Field label="Responder o anotar"><textarea className="textarea" value={nota.body} onChange={(e) => setNota({ ...nota, body: e.target.value })} placeholder="Qué se le dijo al cliente o qué se encontró." /></Field>
                <PhotoStrip value={nota.photos} onChange={(photos) => setNota({ ...nota, photos })} label="Foto" />
                <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
                  <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={nota.internal} onChange={(e) => setNota({ ...nota, internal: e.target.checked })} />Nota interna (no cuenta como respuesta al cliente)</label>
                  <input className="input input--mono" style={{ maxWidth: 110 }} inputMode="decimal" placeholder="horas" value={nota.hours} onChange={(e) => setNota({ ...nota, hours: e.target.value })} />
                  <button className="btn btn--crimson btn--sm" onClick={responder} disabled={!nota.body.trim()}>Guardar</button>
                  {open.status !== "esperando_cliente" && <button className="btn btn--ghost btn--sm" onClick={() => mover("esperando_cliente")}>Esperando al cliente</button>}
                </div>
              </>
            )}
          </Card>
        </Modal>
      )}

      {cerrar && open && (
        <Modal title={`Resolver ${open.number}`} onClose={() => setCerrar(null)} foot={<>
          <button className="btn btn--ghost" onClick={() => setCerrar(null)}>Cancelar</button>
          <button className="btn btn--crimson" onClick={() => mover("resuelto", { solution: cerrar.solution, hours: Number(cerrar.hours || 0), billable: cerrar.billable, amount: Number(cerrar.amount || 0) })} disabled={cerrar.solution.trim().length < 3}>Marcar resuelto</button>
        </>}>
          <Field label="¿Qué se hizo?" hint="Queda en el historial del equipo y del cliente."><textarea className="textarea" autoFocus value={cerrar.solution} onChange={(e) => setCerrar({ ...cerrar, solution: e.target.value })} placeholder="Se cambió la fuente de 12 V, quedó grabando y se probó con el cliente." /></Field>
          <div className="grid-3">
            <Field label="Horas"><input className="input input--mono" inputMode="decimal" value={cerrar.hours} onChange={(e) => setCerrar({ ...cerrar, hours: e.target.value })} /></Field>
            <Field label="¿Se cobra?"><select className="select" value={cerrar.billable ? "1" : "0"} onChange={(e) => setCerrar({ ...cerrar, billable: e.target.value === "1" })}><option value="0">No (garantía o contrato)</option><option value="1">Sí</option></select></Field>
            {cerrar.billable && <Field label="Monto"><input className="input input--mono" inputMode="decimal" value={cerrar.amount} onChange={(e) => setCerrar({ ...cerrar, amount: e.target.value })} /></Field>}
          </div>
        </Modal>
      )}

      {visita && open && (
        <Modal title="Mandar un técnico al sitio" onClose={() => setVisita(null)} foot={<>
          <button className="btn btn--ghost" onClick={() => setVisita(null)}>Cancelar</button>
          <button className="btn btn--crimson" onClick={mandarTecnico}>Crear orden de trabajo</button>
        </>}>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Se crea con el cliente, el sitio y la descripción de este ticket. El técnico la ve en «Mi día».</p>
          <div className="grid-2">
            <Field label="Técnico"><select className="select" value={visita.technician_id} onChange={(e) => setVisita({ ...visita, technician_id: e.target.value })}><option value="">Sin asignar</option>{(meta?.agents || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
            <Field label="Fecha y hora"><input className="input" type="datetime-local" value={visita.scheduled_at} onChange={(e) => setVisita({ ...visita, scheduled_at: e.target.value })} /></Field>
          </div>
          <Field label="Sitio"><input className="input" value={visita.site} onChange={(e) => setVisita({ ...visita, site: e.target.value })} /></Field>
        </Modal>
      )}

      {form && (
        <Modal title={form.id ? "Editar ticket" : "Nuevo ticket"} onClose={() => setForm(null)} wide foot={<>
          <button className="btn btn--ghost" onClick={() => setForm(null)}>Cancelar</button>
          <button className="btn btn--crimson" onClick={save} disabled={form.subject.trim().length < 3}>Guardar</button>
        </>}>
          <Field label="Asunto"><input className="input" autoFocus value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} placeholder="No graba la cámara del portón" /></Field>
          <div className="grid-3">
            <Field label="Cliente"><Lookup value={form.customer_name} placeholder="Buscar cliente…" fetcher={searchCustomers} onSelect={(it, text) => setForm({ ...form, customer_id: it ? String(it.id) : "", customer_name: text })} /></Field>
            <Field label="Tipo"><select className="select" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{(meta?.kinds || []).map((k) => <option key={k} value={k}>{KIND[k] || k}</option>)}</select></Field>
            <Field label="Canal"><select className="select" value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>{(meta?.channels || []).map((c) => <option key={c} value={c}>{c}</option>)}</select></Field>
            <Field label="Prioridad" hint={meta ? `Primera respuesta: ${meta.sla_horas[form.priority]} h` : undefined}><select className="select" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>{(meta?.priorities || []).map((x) => <option key={x} value={x}>{x}</option>)}</select></Field>
            <Field label="Nivel" hint="1 atiende · 2 diagnostica · 3 escala al fabricante"><select className="select" value={form.level} onChange={(e) => setForm({ ...form, level: Number(e.target.value) })}><option value={1}>Nivel 1</option><option value={2}>Nivel 2</option><option value={3}>Nivel 3</option></select></Field>
            <Field label="Responsable"><select className="select" value={form.assigned_to} onChange={(e) => setForm({ ...form, assigned_to: e.target.value })}><option value="">Sin asignar</option>{(meta?.agents || []).map((a) => <option key={a.id} value={a.id}>{a.name}{a.id === me?.user.id ? " (yo)" : ""}</option>)}</select></Field>
          </div>
          <Field label="Qué reporta el cliente"><textarea className="textarea" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
        </Modal>
      )}
    </>
  );
}
