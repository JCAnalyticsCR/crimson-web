/* Detalle de una oportunidad en común: partes, contacto del sitio, quién la lleva en Crimson, su participación
   (papel, alcance, partidas, contactos), los precios que Crimson le pidió, "Enviar mis precios" y sus levantamientos.
   Reunión 2 (pedido 14, "muy básico"): además la etapa con la próxima acción y su fecha, el alcance como lista, los
   seguimientos que Crimson compartió (pedido 15) y la ejecución del proyecto en común (pedido 17).
   Nunca montos de Crimson, costos ni márgenes ni otros aliados; el total de la cotización solo si Crimson lo habilitó. */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { Badge, I, Icon } from "../../ui/components";
import QuoteForm from "./QuoteForm";
import AllyExecution from "./AllyExecution";
import { ESTADO_OPP, ESTADO_PRECIO, ROL_PARTICIPACION, errorText, useAlly, type OppFull, type Req } from "./shared";

/* El embudo de Crimson en palabras del aliado: dónde va la oportunidad (sin probabilidad ni montos). */
const ETAPAS = ["nuevo", "contactado", "requiere_visita", "levantamiento", "cotizando", "enviada", "negociacion", "ganada"];
const FUNCION: Record<string, string> = { comercial: "Comercial", tecnico: "Técnico", aprobador: "Aprobador", pagos: "Pagos" };
const vence = (s: string | null) => (s ? Math.round((new Date(`${s}T12:00:00`).getTime() - Date.now()) / 86400000) : null);
/** El alcance llega como texto: una línea por punto (sin viñetas repetidas). */
const lineas = (t: string | null) => (t || "").split("\n").map((x) => x.replace(/^[\s•\-*·]+/, "").trim()).filter(Boolean);

export function RequestList({ rows, onRespond }: { rows: Req[]; onRespond?: (r: Req) => void }) {
  if (!rows.length) return <p className="muted">Crimson no le ha pedido precios aquí.</p>;
  return (
    <div className="cp-list">
      {rows.map((r) => (
        <div key={r.id} className="cp-row">
          <div className="cp-row__main">
            <b>{r.what}</b>
            <div className="cp-row__meta">
              {r.source === "aliado" ? "Enviado por usted" : `Pedido por Crimson${r.responsible ? ` (${r.responsible})` : ""}`}
              {r.due_date ? ` · antes del ${fmtDate(r.due_date)}` : ""}
              {r.amount != null ? ` · su precio: ${fmtMoney(r.amount, r.currency)}` : ""}
              {r.responded_at ? ` · respondido el ${fmtDate(r.responded_at.slice(0, 10))}` : ""}
            </div>
          </div>
          <div className="cp-row__side">
            <span className={`badge badge--${ESTADO_PRECIO[r.status]?.tone || "muted"}`}>{ESTADO_PRECIO[r.status]?.label || r.status}</span>
            {r.can_respond && onRespond && <button className="btn btn--soft btn--sm" onClick={() => onRespond(r)}>{r.status === "recibido" ? "Corregir" : "Responder"}</button>}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function AllyOpportunity() {
  const { id } = useParams();
  const nav = useNavigate();
  const { readOnly, can } = useAlly();
  const [o, setO] = useState<OppFull | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [resp, setResp] = useState<Req | null>(null);
  const [enviar, setEnviar] = useState(false);
  const load = useCallback(() => { api<OppFull>(`/aliado/oportunidades/${id}`).then(setO).catch((e) => setErr(errorText(e))); }, [id]);
  useEffect(load, [load]);
  if (err) return <p className="muted">{err}</p>;
  if (!o) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const precios = can("precios") && !readOnly;
  return (
    <>
      <div className="cp-h">
        <div><div className="meta">{o.number} · {ESTADO_OPP[o.status] || o.status}</div><h1 className="h1">{o.title}</h1></div>
        <div className="al-actions">
          {precios && <button className="btn btn--soft" onClick={() => setEnviar(true)}><Icon d={I.plus} />Enviar mis precios</button>}
          {o.can_survey && !readOnly && <button className="btn btn--crimson" onClick={() => nav(`/levantamientos?nuevo=1&oportunidad=${o.id}`)}><Icon d={I.ruler} />Hacer levantamiento</button>}
        </div>
      </div>
      <div className="cp-card" style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
          <b>Etapa: {ESTADO_OPP[o.status] || o.status}</b>
          {o.cotizacion && <span className="meta">Cotización {o.cotizacion.number} · total {fmtMoney(o.cotizacion.total, o.cotizacion.currency)}</span>}
        </div>
        {o.status !== "perdida" && (
          <div className="al-steps" aria-label="Avance de la oportunidad">
            {ETAPAS.map((e, i) => <span key={e} className={i <= ETAPAS.indexOf(o.status) ? "is-done" : ""} title={ESTADO_OPP[e]} />)}
          </div>
        )}
        <dl className="al-kv" style={{ marginTop: 8 }}>
          <dt>Próxima acción</dt>
          <dd>
            {o.next_action || "Sin próxima acción definida"}
            {o.next_action_date && <span style={{ color: (vence(o.next_action_date) ?? 1) < 0 ? "var(--bad)" : undefined }}> · {fmtDate(o.next_action_date)}</span>}
          </dd>
        </dl>
      </div>
      <div className="cp-grid cp-grid--2">
        <div className="cp-card">
          <dl className="al-kv">
            <dt>Contratante</dt><dd>{o.customer || "—"}</dd>
            {o.end_customer && <><dt>Cliente final</dt><dd>{o.end_customer}</dd></>}
            <dt>Sitio</dt><dd>{o.site || "—"}</dd>
            <dt>Contacto en sitio</dt><dd>{o.contact.name || "—"}{o.contact.phone ? ` · ${o.contact.phone}` : ""}</dd>
            <dt>En Crimson</dt><dd>{o.crimson_owner || "—"}</dd>
            {o.project && <><dt>Proyecto</dt><dd><Link to={`/proyectos/${o.project.id}`}>{o.project.number}</Link> · <Badge status={o.project.status} /></dd></>}
          </dl>
        </div>
        <div className="cp-card">
          <b>Mi participación · lo que le toca</b>
          {o.participations.map((p) => {
            const alcance = [...lineas(p.scope), ...p.scope_lines];
            return (
              <div key={p.id} style={{ marginTop: 8 }}>
                <span className="badge badge--info">{ROL_PARTICIPACION[p.role] || p.role}</span>
                {alcance.length ? <ul style={{ margin: "6px 0", paddingLeft: 18 }}>{alcance.map((l, i) => <li key={`${i}-${l}`}>{l}</li>)}</ul> : <p className="muted" style={{ margin: "6px 0" }}>Crimson todavía no detalló el alcance.</p>}
                {Object.entries(p.contacts).map(([k, c]) => <div key={k} className="meta">{FUNCION[k] || k}: {c.name || "—"}{c.phone ? ` · ${c.phone}` : ""}{c.email ? ` · ${c.email}` : ""}</div>)}
              </div>
            );
          })}
        </div>
      </div>
      <h2 className="h3 al-sec">Seguimientos de Crimson</h2>
      {o.seguimientos?.length ? (
        <ol className="cp-list al-log">
          {o.seguimientos.map((x, i) => (
            <li key={i} className="cp-row"><div className="cp-row__main">{x.when && <div className="cp-row__meta">{x.when}{x.author ? ` · ${x.author}` : ""}</div>}<span>{x.text}</span></div></li>
          ))}
        </ol>
      ) : <p className="muted">Crimson todavía no ha compartido seguimientos de esta oportunidad.</p>}
      <h2 className="h3 al-sec">Precios que le pedimos</h2>
      <RequestList rows={o.requests} onRespond={precios ? setResp : undefined} />
      {resp && (
        <div style={{ marginTop: 12 }}>
          <div className="meta" style={{ marginBottom: 6 }}>Responder: {resp.what}</div>
          <QuoteForm path={`/aliado/solicitudes/${resp.id}`} method="PUT" onCancel={() => setResp(null)} onDone={() => { setResp(null); load(); }} />
        </div>
      )}
      {enviar && (
        <div style={{ marginTop: 12 }}>
          <div className="meta" style={{ marginBottom: 6 }}>Enviar un precio por iniciativa propia</div>
          <QuoteForm path={`/aliado/oportunidades/${o.id}/precios`} method="POST" ask roles={o.participations.map((p) => ({ id: p.id, role: p.role }))} onCancel={() => setEnviar(false)} onDone={() => { setEnviar(false); load(); }} />
        </div>
      )}
      <h2 className="h3 al-sec">Levantamientos de mi empresa</h2>
      {o.surveys.length ? (
        <div className="cp-list">
          {o.surveys.map((s) => (
            <Link key={s.id} to={`/levantamientos/${s.id}`} className="cp-row">
              <div className="cp-row__main">
                <b>{s.number}</b>
                <div className="cp-row__meta">{s.kind}{s.technician ? ` · ${s.technician}` : ""}{s.visit_date ? ` · visita ${fmtDate(s.visit_date)}` : ""}</div>
              </div>
              <div className="cp-row__side"><Badge status={s.review_status || s.status} /></div>
            </Link>
          ))}
        </div>
      ) : <p className="muted">Todavía no hay levantamientos de su empresa en esta oportunidad.</p>}
      {o.ejecucion && (
        <>
          <h2 className="h3 al-sec">Ejecución del proyecto</h2>
          <AllyExecution e={o.ejecucion} showProject />
        </>
      )}
    </>
  );
}
