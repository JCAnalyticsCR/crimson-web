/* Oportunidades: el embudo antes de la cotizacion.
   Lo que importa aqui no es el monto sino la proxima accion con fecha: es lo que evita que un negocio se enfrie. */
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import { Lookup, searchCustomers } from "../../ui/Lookup";
import { ArchiveActions } from "../../ui/ArchiveActions";
import "./opportunities.css";

export type Opp = {
  id: number; number: string; title: string; customer_id: number | null; customer: string | null; source: string | null; solution: string | null;
  owner_id: number | null; owner: string | null; amount: string; currency: string; probability: number; status: string;
  next_action: string | null; next_action_date: string | null; lost_reason: string | null; notes: string | null;
  quote_id: number | null; project_id: number | null; weighted: string; created_at: string;
  archived_at?: string | null; trashed_at?: string | null;
};
type Detail = Opp & {
  surveys: { id: number; number: string; kind: string; status: string }[];
  quote?: { id: number; number: string; status: string; total: string } | null;
  project?: { id: number; number: string; status: string } | null;
};
type Board = { columns: { status: string; count: number; amount: string; weighted: string; items: Opp[] }[]; total: string; weighted: string };
type Pending = Opp & { days_late: number };
type Meta = { states: string[]; sources: string[]; solutions: string[]; sellers: { id: number; name: string }[]; can_see_all: boolean };

export const OPP_STATES: Record<string, string> = {
  nuevo: "Nuevo", contactado: "Contactado", requiere_visita: "Requiere visita", levantamiento: "En levantamiento",
  cotizando: "Cotizando", enviada: "Cotización enviada", negociacion: "Negociación", ganada: "Ganada", perdida: "Perdida",
};
const SOLUTIONS: Record<string, string> = {
  cctv: "CCTV", redes: "Redes / WiFi", acceso: "Control de acceso", asistencia: "Tiempo y asistencia",
  ups: "Respaldo eléctrico", cableado: "Cableado estructurado", anpr: "ANPR / barreras", otro: "Otro",
};
const ID_TYPES: Record<string, string> = { fisica: "Física", juridica: "Jurídica", dimex: "DIMEX", nite: "NITE", extranjero: "Extranjero" };

const blank = { title: "", customer_id: "", customer_name: "", source: "", solution: "", amount: "0", probability: 30, next_action: "", next_action_date: "", owner_id: "", notes: "" };
const blankTouch = { note: "", next_action: "", next_action_date: "", status: "" };
const blankCust = { name: "", id_type: "fisica", id_number: "", phone: "", email: "" };

/** Días que faltan para la próxima acción (negativo = ya venció). */
const dueIn = (s: string | null) => (s ? Math.round((new Date(s + "T12:00:00").getTime() - Date.now()) / 86400000) : null);

/** La bitácora vive en notes, una línea por seguimiento ("fecha · autor: texto"), la más nueva arriba. */
const logLines = (notes: string | null) =>
  (notes || "").split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const m = l.match(/^(\d{2}\/\d{2}\/\d{4} \d{2}:\d{2}) · ([^:]+): (.*)$/);
    return m ? { when: m[1], who: m[2], text: m[3] } : { when: "", who: "", text: l };
  });

export default function Opportunities() {
  const { toast, allows } = useSession();
  const nav = useNavigate();
  const [view, setView] = useState<"board" | "list" | "pending">("board");
  const [board, setBoard] = useState<Board | null>(null);
  const [list, setList] = useState<Opp[]>([]);
  const [pending, setPending] = useState<Pending[]>([]);
  const [team, setTeam] = useState(false);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [q, setQ] = useState("");
  const [mine, setMine] = useState(false);
  const [open, setOpen] = useState<Detail | null>(null);
  const [form, setForm] = useState<(typeof blank & { id?: number }) | null>(null);
  const [touch, setTouch] = useState(blankTouch);
  const [touchOpen, setTouchOpen] = useState(false);
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [newCust, setNewCust] = useState<typeof blankCust | null>(null);
  const canEdit = allows("crm_pipeline.editar");

  const load = useCallback(() => {
    api<Board>("/opportunities/board").then(setBoard).catch(() => setBoard(null));
    api<Opp[]>(`/opportunities?limit=200${mine ? "&mine=true" : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`).then(setList);
    api<Pending[]>(`/opportunities/pending${team ? "?everyone=true" : ""}`).then(setPending).catch(() => setPending([]));
  }, [q, mine, team]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);
  useEffect(() => {
    api<Meta>("/opportunities/meta/config").then(setMeta);
  }, []);

  const show = async (id: number) => { setOpen(await api<Detail>(`/opportunities/${id}`)); setTouch(blankTouch); setTouchOpen(false); };
  // /oportunidades?id=<n>: se abre desde Archivo (o un enlace) sin buscarla en el embudo
  const [params, setParams] = useSearchParams();
  const linked = params.get("id");
  useEffect(() => { if (linked) show(Number(linked)).catch(() => undefined); }, [linked]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    if (!form) return;
    const body = {
      title: form.title, customer_id: form.customer_id ? Number(form.customer_id) : null, source: form.source || null, solution: form.solution || null,
      owner_id: form.owner_id ? Number(form.owner_id) : null, amount: Number(form.amount || 0), probability: Number(form.probability),
      next_action: form.next_action || null, next_action_date: form.next_action_date || null, notes: form.notes || null,
      ...(form.id ? { status: open?.status } : {}),
    };
    try {
      const saved = await api<Opp>(form.id ? `/opportunities/${form.id}` : "/opportunities", { method: form.id ? "PUT" : "POST", json: body });
      toast(form.id ? "Oportunidad actualizada" : `Oportunidad ${saved.number} creada`);
      setForm(null); setNewCust(null); load(); if (form.id) show(form.id);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const sendTouch = async () => {
    if (!open || touch.note.trim().length < 2) return;
    try {
      await api(`/opportunities/${open.id}/touch`, { method: "POST", json: { note: touch.note.trim(), next_action: touch.next_action || null, next_action_date: touch.next_action_date || null, status: touch.status || null } });
      toast("Seguimiento registrado"); await show(open.id); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  /* Arrastre entre columnas: la tarjeta se mueve de una vez (optimista) y se registra con el mismo endpoint de
     seguimiento, que deja la linea en la bitacora y la auditoria del cambio de etapa. Si el servidor dice que no
     (permiso, oportunidad ajena), el embudo vuelve a como estaba. */
  const move = async (id: number, to: string) => {
    if (!board) return;
    const from = board.columns.find((c) => c.items.some((o) => o.id === id));
    const item = from?.items.find((o) => o.id === id);
    if (!from || !item || from.status === to) return;
    const prev = board;
    const amt = Number(item.amount) || 0;
    setBoard({
      ...board,
      columns: board.columns.map((c) =>
        c.status === from.status ? { ...c, count: c.count - 1, amount: String(Number(c.amount) - amt), items: c.items.filter((o) => o.id !== id) }
        : c.status === to ? { ...c, count: c.count + 1, amount: String(Number(c.amount) + amt), items: [{ ...item, status: to }, ...c.items] }
        : c),
    });
    try {
      await api(`/opportunities/${id}/touch`, { method: "POST", json: { note: `Movida de ${OPP_STATES[from.status] || from.status} a ${OPP_STATES[to] || to} (embudo)`, status: to } });
      toast(`${item.title} → ${OPP_STATES[to] || to}`);
      load();
    } catch (e) {
      setBoard(prev);
      toast(e instanceof Error ? `No se pudo mover: ${e.message}` : "No se pudo mover", "bad");
    }
  };

  /* Cliente nuevo sin salir de la oportunidad: el mismo POST /customers de la ficha de clientes, y queda elegido. */
  const createCustomer = async () => {
    if (!newCust || !form || newCust.name.trim().length < 2) return;
    try {
      const c = await api<{ id: number; name: string }>("/customers", {
        method: "POST",
        json: { name: newCust.name.trim(), id_type: newCust.id_type, id_number: newCust.id_number.trim() || null, phone: newCust.phone.trim() || null, email: newCust.email.trim() || null },
      });
      setForm({ ...form, customer_id: String(c.id), customer_name: c.name });
      setNewCust(null);
      toast(`Cliente ${c.name} creado`);
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo crear el cliente", "bad"); }
  };

  const card = (o: Opp) => {
    const dias = dueIn(o.next_action_date);
    const tone = dias === null ? "" : dias < 0 ? " opp--late" : dias <= 2 ? " opp--soon" : "";
    return (
      <div
        key={o.id}
        role="button"
        tabIndex={0}
        className={`opp${tone}${drag === o.id ? " is-dragging" : ""}`}
        draggable={canEdit}
        onDragStart={(e) => { e.dataTransfer.setData("text/plain", String(o.id)); e.dataTransfer.effectAllowed = "move"; setDrag(o.id); }}
        onDragEnd={() => { setDrag(null); setOver(null); }}
        onClick={() => show(o.id)}
        onKeyDown={(e) => { if (e.key === "Enter") show(o.id); }}
      >
        <b>{o.title}</b>
        <div className="opp__meta"><span>{o.customer || "Sin cliente"}</span><span className="money">{fmtMoney(o.amount, o.currency)}</span></div>
        <div className="opp__meta">
          <span>{o.next_action ? `${o.next_action}${o.next_action_date ? ` · ${fmtDate(o.next_action_date)}` : ""}` : "Sin próxima acción"}</span>
          <span>{o.probability}%</span>
        </div>
        {canEdit && board && (
          /* Respaldo táctil: en pantallas sin mouse no hay arrastre HTML5; este selector solo se ve ahí (opportunities.css). */
          <select className="select opp__move" value={o.status} aria-label="Mover a otra etapa" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()} onChange={(e) => move(o.id, e.target.value)}>
            {board.columns.map((c) => <option key={c.status} value={c.status}>{c.status === o.status ? "Mover a…" : OPP_STATES[c.status] || c.status}</option>)}
          </select>
        )}
      </div>
    );
  };

  const log = open ? logLines(open.notes) : [];

  return (
    <>
      <div className="page-head">
        <div><div className="meta">06 · Comercial</div><h1 className="h1">Oportunidades</h1></div>
        <div className="page-head__actions">
          <div className="tabs">
            <button className={view === "board" ? "is-active" : ""} onClick={() => setView("board")}>Embudo</button>
            <button className={view === "list" ? "is-active" : ""} onClick={() => setView("list")}>Lista</button>
            <button className={view === "pending" ? "is-active" : ""} onClick={() => setView("pending")}>Mis pendientes{pending.length ? ` (${pending.length})` : ""}</button>
          </div>
          {allows("crm_pipeline.crear") && <button className="btn btn--crimson" onClick={() => setForm({ ...blank })}><Icon d={I.plus} />Nueva oportunidad</button>}
        </div>
      </div>

      {board && (
        <div className="eco">
          <div className="eco__box"><span className="meta">Embudo abierto</span><b className="money">{fmtMoney(board.total)}</b></div>
          <div className="eco__box"><span className="meta">Ponderado por probabilidad</span><b className="money">{fmtMoney(board.weighted)}</b></div>
          <div className="eco__box"><span className="meta">Oportunidades</span><b>{board.columns.reduce((s, c) => s + c.count, 0)}</b></div>
          <button type="button" className="eco__box is-bad opp-eco-link" onClick={() => setView("pending")} title="Ver mis pendientes">
            <span className="meta">Con seguimiento vencido</span><b>{list.filter((o) => (dueIn(o.next_action_date) ?? 1) < 0 && !["ganada", "perdida"].includes(o.status)).length}</b>
          </button>
        </div>
      )}

      {view === "board" ? (
        !board ? <div style={{ padding: 40, textAlign: "center" }}><span className="spinner" /></div> : (
          <>
            {canEdit && <p className="muted opp-hint">Arrastrá una tarjeta a otra columna para cambiarla de etapa; queda anotado en la bitácora.</p>}
            <div className="board">
              {board.columns.map((c) => (
                <div
                  className={`board__col${over === c.status && drag !== null ? " is-over" : ""}`}
                  key={c.status}
                  onDragOver={(e) => { if (drag === null) return; e.preventDefault(); e.dataTransfer.dropEffect = "move"; if (over !== c.status) setOver(c.status); }}
                  onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(null); }}
                  onDrop={(e) => { e.preventDefault(); const id = Number(e.dataTransfer.getData("text/plain")) || drag; setOver(null); setDrag(null); if (id) move(id, c.status); }}
                >
                  <div className="board__head"><b>{OPP_STATES[c.status] || c.status}</b><span>{c.count} · {fmtMoney(c.amount)}</span></div>
                  {c.items.length === 0 ? <p className="muted" style={{ fontSize: 12.5, padding: "0 4px" }}>Nada aquí.</p> : c.items.map(card)}
                </div>
              ))}
            </div>
          </>
        )
      ) : view === "pending" ? (
        <Card flush>
          <div className="list-head">
            <span className="muted" style={{ fontSize: 13 }}>Próximas acciones de hoy y vencidas, de la más atrasada a la de hoy. Cada mañana (lunes a viernes) llegan también por correo al responsable.</span>
            {meta?.can_see_all && <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13, marginLeft: "auto", whiteSpace: "nowrap" }}><input type="checkbox" checked={team} onChange={(e) => setTeam(e.target.checked)} />Todo el equipo</label>}
          </div>
          {pending.length === 0 ? <Empty hint="Nada pendiente para hoy. Las oportunidades con la próxima acción de hoy o vencida aparecen aquí." /> : (
            <table className="table">
              <thead><tr><th>Fecha</th><th>Próxima acción</th><th>Oportunidad</th><th>Cliente</th><th>Responsable</th><th /></tr></thead>
              <tbody>{pending.map((o) => (
                <tr key={o.id} style={{ cursor: "pointer" }} onClick={() => show(o.id)}>
                  <td style={{ whiteSpace: "nowrap" }}>{fmtDate(o.next_action_date)}<div className="meta" style={{ color: o.days_late > 0 ? "var(--bad)" : "var(--warn)" }}>{o.days_late > 0 ? `Vencida hace ${o.days_late} d` : "Hoy"}</div></td>
                  <td style={{ fontWeight: 600 }}>{o.next_action || "—"}</td>
                  <td><span className="mono muted">{o.number}</span> · {o.title}</td>
                  <td className="muted">{o.customer || "—"}</td>
                  <td className="muted">{o.owner || "—"}</td>
                  <td className="num"><button className="btn btn--ghost btn--sm" onClick={(e) => { e.stopPropagation(); show(o.id); }}>Abrir</button></td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </Card>
      ) : (
        <Card flush>
          <div className="list-head">
            <div className="search" style={{ maxWidth: 380 }}><Icon d={I.search} size={16} /><input placeholder="Título o número…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
            {meta?.can_see_all && <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} />Solo las mías</label>}
            <button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} /></button>
          </div>
          {list.length === 0 ? <Empty hint="Cada visita, llamada o referido entra aquí antes de convertirse en cotización." /> : (
            <table className="table">
              <thead><tr><th>Número</th><th>Título</th><th>Cliente</th><th>Estado</th><th className="num">Monto</th><th>Próxima acción</th><th>Responsable</th><th /></tr></thead>
              <tbody>{list.map((o) => {
                const dias = dueIn(o.next_action_date);
                return (
                  <tr key={o.id}>
                    <td className="mono muted">{o.number}</td>
                    <td style={{ fontWeight: 600 }}>{o.title}</td>
                    <td className="muted">{o.customer || "—"}</td>
                    <td><span className="badge badge--info">{OPP_STATES[o.status] || o.status}</span></td>
                    <td className="num money">{fmtMoney(o.amount, o.currency)}</td>
                    <td style={{ fontSize: 13, color: dias !== null && dias < 0 ? "var(--bad)" : undefined }}>{o.next_action ? `${o.next_action}${o.next_action_date ? ` · ${fmtDate(o.next_action_date)}` : ""}` : "—"}</td>
                    <td className="muted">{o.owner || "—"}</td>
                    <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => show(o.id)}>Ver</button></td>
                  </tr>
                );
              })}</tbody>
            </table>
          )}
        </Card>
      )}

      {open && (
        <Modal title={`${open.number} · ${open.title}`} onClose={() => { setOpen(null); if (linked) setParams({}); }} wide foot={<>
          <div className="opp-foot__links">
            <ArchiveActions kind="opportunity" id={open.id} number={open.number} archivedAt={open.archived_at} trashedAt={open.trashed_at} onDone={() => { setOpen(null); if (linked) setParams({}); load(); }} />
            {allows("field.crear") && !open.surveys.length && <button className="btn btn--soft" onClick={() => nav(`/levantamientos?nuevo=${open.id}`)}><Icon d={I.box} />Levantamiento técnico</button>}
            {open.quote && <button className="btn btn--soft" onClick={() => nav(`/cotizaciones/${open.quote!.id}`)}>Ver cotización</button>}
            {open.project && <button className="btn btn--soft" onClick={() => nav(`/proyectos/${open.project!.id}`)}>Ver proyecto</button>}
          </div>
          {canEdit && <button className="btn btn--ghost" onClick={() => setForm({ id: open.id, title: open.title, customer_id: open.customer_id ? String(open.customer_id) : "", customer_name: open.customer || "", source: open.source || "", solution: open.solution || "", amount: String(open.amount), probability: open.probability, next_action: open.next_action || "", next_action_date: open.next_action_date || "", owner_id: open.owner_id ? String(open.owner_id) : "", notes: open.notes || "" })}>Editar</button>}
          <button className="btn btn--crimson" onClick={() => setOpen(null)}>Cerrar</button>
        </>}>
          <div className="opp-detail">
            {/* Bitácora: es lo que más se consulta. Columna lateral en escritorio, arriba en el celular (opportunities.css). */}
            <section className="opp-log" aria-label="Bitácora">
              <div className="opp-log__head">
                <b>Bitácora</b>
                {canEdit && !touchOpen && <button className="btn btn--crimson btn--sm" onClick={() => setTouchOpen(true)}><Icon d={I.plus} />Registrar seguimiento</button>}
              </div>
              {log.length === 0 ? <p className="muted" style={{ fontSize: 13, margin: 0 }}>Sin seguimientos todavía.</p> : (
                <ol className="opp-log__list">
                  {log.map((l, i) => (
                    <li key={i}>{l.when && <span className="meta">{l.when} · {l.who}</span>}<p>{l.text}</p></li>
                  ))}
                </ol>
              )}
            </section>

            <div className="opp-main">
              {canEdit && touchOpen && (
                <Card title="Registrar seguimiento" extra={<button className="btn btn--ghost btn--sm" onClick={() => { setTouchOpen(false); setTouch(blankTouch); }}>Cancelar</button>}>
                  <Field label="¿Qué pasó?" hint="Llamada, visita, correo… Queda con fecha y tu nombre en la bitácora.">
                    <textarea className="textarea" autoFocus style={{ minHeight: 64 }} value={touch.note} onChange={(e) => setTouch({ ...touch, note: e.target.value })} placeholder="Llamé a don Andrés; pide propuesta con 8 cámaras…" />
                  </Field>
                  <div className="grid-3">
                    <Field label="Próxima acción"><input className="input" value={touch.next_action} onChange={(e) => setTouch({ ...touch, next_action: e.target.value })} placeholder="Enviar cotización" /></Field>
                    <Field label="Fecha"><input className="input" type="date" value={touch.next_action_date} onChange={(e) => setTouch({ ...touch, next_action_date: e.target.value })} /></Field>
                    <Field label="Mover a"><select className="select" value={touch.status} onChange={(e) => setTouch({ ...touch, status: e.target.value })}><option value="">Dejar igual</option>{(meta?.states || []).map((s) => <option key={s} value={s}>{OPP_STATES[s] || s}</option>)}</select></Field>
                  </div>
                  <div><button className="btn btn--crimson btn--sm" onClick={sendTouch} disabled={touch.note.trim().length < 2}>Guardar seguimiento</button></div>
                </Card>
              )}
              <div className="eco">
                <div className="eco__box"><span className="meta">Monto{open.quote ? ` · ${open.quote.number}` : ""}</span><b className="money">{fmtMoney(open.amount, open.currency)}</b></div>
                <div className="eco__box"><span className="meta">Probabilidad</span><b>{open.probability}%</b></div>
                <div className="eco__box"><span className="meta">Ponderado</span><b className="money">{fmtMoney(open.weighted, open.currency)}</b></div>
                <div className="eco__box"><span className="meta">Estado</span><b style={{ fontSize: 15 }}>{OPP_STATES[open.status] || open.status}</b></div>
              </div>
              <div className="opp-next">
                <span className="meta">Próxima acción</span>
                <b>{open.next_action || "Sin próxima acción"}</b>
                {open.next_action_date && <span style={{ color: (dueIn(open.next_action_date) ?? 1) < 0 ? "var(--bad)" : undefined }}>{fmtDate(open.next_action_date)}</span>}
              </div>
              <div className="grid-3">
                <Field label="Cliente"><input className="input" readOnly value={open.customer || "—"} /></Field>
                <Field label="Solución"><input className="input" readOnly value={SOLUTIONS[open.solution || ""] || "—"} /></Field>
                <Field label="Origen"><input className="input" readOnly value={open.source || "—"} /></Field>
              </div>
              {open.surveys.length > 0 && (
                <p style={{ fontSize: 13, margin: 0 }}>Levantamientos: {open.surveys.map((s) => <button key={s.id} className="btn btn--ghost btn--sm" style={{ marginRight: 6 }} onClick={() => nav(`/levantamientos?id=${s.id}`)}>{s.number} · {s.status}</button>)}</p>
              )}
            </div>
          </div>
        </Modal>
      )}

      {form && (
        <Modal title={form.id ? "Editar oportunidad" : "Nueva oportunidad"} onClose={() => { setForm(null); setNewCust(null); }} wide foot={<>
          <button className="btn btn--ghost" onClick={() => { setForm(null); setNewCust(null); }}>Cancelar</button>
          <button className="btn btn--crimson" onClick={save} disabled={form.title.trim().length < 2 || !!newCust}>Guardar</button>
        </>}>
          <Field label="Título" hint="Cómo lo reconoce el equipo: obra, condominio, empresa."><input className="input" autoFocus value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Condominio Los Robles · CCTV" /></Field>
          <div className="grid-3">
            <Field label="Cliente" hint={allows("crm.crear") ? "Escribí para buscar; si no existe, crealo desde la lista. Puede quedar sin cliente si todavía es un prospecto." : "Escribí para buscar; puede quedar sin cliente si todavía es un prospecto."}>
              <Lookup
                value={form.customer_name}
                placeholder="Buscar cliente…"
                fetcher={searchCustomers}
                onSelect={(it, text) => setForm({ ...form, customer_id: it ? String(it.id) : "", customer_name: text })}
                onCreate={allows("crm.crear") ? (text) => setNewCust({ ...blankCust, name: text }) : undefined}
                createLabel="Crear cliente"
              />
            </Field>
            <Field label="Solución"><select className="select" value={form.solution} onChange={(e) => setForm({ ...form, solution: e.target.value })}><option value="">—</option>{(meta?.solutions || []).map((s) => <option key={s} value={s}>{SOLUTIONS[s] || s}</option>)}</select></Field>
            <Field label="Origen"><select className="select" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}><option value="">—</option>{(meta?.sources || []).map((s) => <option key={s} value={s}>{s}</option>)}</select></Field>
          </div>
          {newCust && (
            <div className="opp-newcust">
              <div className="opp-newcust__head"><b>Cliente nuevo</b><span className="muted">Se crea en Clientes y queda elegido en esta oportunidad.</span></div>
              <div className="grid-3">
                <Field label="Nombre"><input className="input" autoFocus value={newCust.name} onChange={(e) => setNewCust({ ...newCust, name: e.target.value })} /></Field>
                <Field label="Tipo de identificación"><select className="select" value={newCust.id_type} onChange={(e) => setNewCust({ ...newCust, id_type: e.target.value })}>{Object.entries(ID_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
                <Field label="Número de identificación"><input className="input input--mono" value={newCust.id_number} onChange={(e) => setNewCust({ ...newCust, id_number: e.target.value })} /></Field>
                <Field label="Teléfono"><input className="input" type="tel" value={newCust.phone} onChange={(e) => setNewCust({ ...newCust, phone: e.target.value })} /></Field>
                <Field label="Correo"><input className="input" type="email" value={newCust.email} onChange={(e) => setNewCust({ ...newCust, email: e.target.value })} /></Field>
              </div>
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                <button className="btn btn--ghost btn--sm" onClick={() => setNewCust(null)}>Descartar</button>
                <button className="btn btn--crimson btn--sm" onClick={createCustomer} disabled={newCust.name.trim().length < 2}>Crear cliente</button>
              </div>
            </div>
          )}
          <div className="grid-3">
            <Field label="Monto estimado" hint={form.id && open?.quote ? `Sigue a la cotización ${open.quote.number}: se actualiza al guardarla.` : undefined}><input className="input input--mono" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></Field>
            <Field label="Probabilidad" hint={`${form.probability}% · pondera el embudo`}><input type="range" min={0} max={100} step={5} value={form.probability} onChange={(e) => setForm({ ...form, probability: Number(e.target.value) })} style={{ width: "100%" }} /></Field>
            {meta?.can_see_all && <Field label="Responsable"><select className="select" value={form.owner_id} onChange={(e) => setForm({ ...form, owner_id: e.target.value })}><option value="">Yo</option>{meta.sellers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>}
            <Field label="Próxima acción"><input className="input" value={form.next_action} onChange={(e) => setForm({ ...form, next_action: e.target.value })} placeholder="Visita técnica" /></Field>
            <Field label="Fecha de la próxima acción"><input className="input" type="date" value={form.next_action_date} onChange={(e) => setForm({ ...form, next_action_date: e.target.value })} /></Field>
          </div>
        </Modal>
      )}
    </>
  );
}
