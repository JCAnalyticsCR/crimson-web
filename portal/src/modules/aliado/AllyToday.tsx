/* "Hoy" (26:03-26:31): lo que le toca hacer al técnico del aliado hoy, en tarjetas grandes para el celular.
   Órdenes de hoy y en curso (las sin fecha dicen "Sin fecha"), levantamientos asignados en borrador o devueltos y
   tickets asignados. Sin precios en ningún lado. El aliado ve lo mismo para todo su equipo (con quién lo hace). */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { Badge, Empty } from "../../ui/components";
import { ESTADO_OPP, ESTADO_TICKET, PRIORIDAD, errorText, fmtWhen, type MyDay, type TechHome } from "./shared";

export function TodayLists({ d, team }: { d: MyDay; team?: boolean }) {
  return (
    <>
      <h2 className="h3 al-sec">Órdenes de trabajo</h2>
      {d.ordenes.length ? (
        <div className="al-big">
          {d.ordenes.map((o) => (
            <Link key={o.id} to={`/ordenes/${o.id}`} className="cp-row">
              <div className="cp-row__main">
                <b>{o.number} · {o.title}</b>
                <div className="cp-row__meta">{o.customer || "—"}{o.site ? ` · ${o.site}` : ""}</div>
                <div className="cp-row__meta">{o.scheduled_at ? fmtWhen(o.scheduled_at) : "Sin fecha"}{team && o.technician ? ` · ${o.technician}` : ""}</div>
              </div>
              <div className="cp-row__side"><Badge status={o.status} /></div>
            </Link>
          ))}
        </div>
      ) : <p className="muted">Nada programado para hoy.</p>}
      <h2 className="h3 al-sec">Levantamientos</h2>
      {d.levantamientos.length ? (
        <div className="cp-list">
          {d.levantamientos.map((s) => (
            <Link key={s.id} to={`/levantamientos/${s.id}`} className="cp-row">
              <div className="cp-row__main">
                <b>{s.number} · {s.kind_label}</b>
                <div className="cp-row__meta">{s.customer || "—"}{s.site ? ` · ${s.site}` : ""}{team && s.technician ? ` · ${s.technician}` : ""}</div>
              </div>
              <div className="cp-row__side">
                {s.review_status === "devuelto" ? <span className="badge badge--warn">Devuelto · {s.open_observations} observación(es)</span> : <Badge status={s.status} />}
              </div>
            </Link>
          ))}
        </div>
      ) : <p className="muted">Sin levantamientos pendientes.</p>}
      <h2 className="h3 al-sec">Soporte</h2>
      {d.tickets.length ? (
        <div className="cp-list">
          {d.tickets.map((t) => (
            <Link key={t.id} to={`/soporte/${t.id}`} className="cp-row">
              <div className="cp-row__main">
                <b>{t.number} · {t.subject}</b>
                <div className="cp-row__meta">{t.customer || "—"} · prioridad {PRIORIDAD[t.priority] || t.priority}{team && t.assigned ? ` · ${t.assigned}` : ""}</div>
              </div>
              <div className="cp-row__side">
                <span className={`badge badge--${t.fuera_de_tiempo ? "bad" : "info"}`}>{ESTADO_TICKET[t.status] || t.status}</span>
              </div>
            </Link>
          ))}
        </div>
      ) : <p className="muted">Sin tickets asignados.</p>}
    </>
  );
}

/* Inicio del técnico aliado (reunión 2, pedido 21: entraba a "Nada asignado para hoy"): además de hoy, sus próximas
   órdenes (se abren el día que tocan), las oportunidades en común de su empresa en resumen y, si no tiene nada, a
   quién acudir. Sin precios ni montos. */
function TechExtra({ t, company }: { t: TechHome; company?: string }) {
  return (
    <>
      <h2 className="h3 al-sec">Próximas órdenes</h2>
      {t.proximas.length ? (
        <div className="cp-list">
          {t.proximas.map((o) => (
            <div key={o.number} className="cp-row" title="Se abre el día que está programada">
              <div className="cp-row__main">
                <b>{o.number} · {o.title}</b>
                <div className="cp-row__meta">{fmtWhen(o.scheduled_at)}{o.customer ? ` · ${o.customer}` : ""}{o.site ? ` · ${o.site}` : ""}</div>
              </div>
              <div className="cp-row__side"><Badge status={o.status} /></div>
            </div>
          ))}
        </div>
      ) : <p className="muted">No tiene órdenes programadas para los próximos días.</p>}
      <h2 className="h3 al-sec">Oportunidades en común{company ? ` · ${company}` : ""}</h2>
      {t.oportunidades.length ? (
        <div className="cp-list">
          {t.oportunidades.map((o) => (
            <div key={o.number} className="cp-row">
              <div className="cp-row__main">
                <b>{o.title}</b>
                <div className="cp-row__meta">{o.number}{o.customer ? ` · ${o.customer}` : ""}{o.site ? ` · ${o.site}` : ""}</div>
              </div>
              <div className="cp-row__side">
                {o.mine && <span className="badge badge--info">Tiene un levantamiento aquí</span>}
                <span className="badge badge--muted">{ESTADO_OPP[o.status] || o.status}</span>
              </div>
            </div>
          ))}
        </div>
      ) : <p className="muted">Su empresa no tiene oportunidades abiertas en común con Crimson en este momento.</p>}
    </>
  );
}

export default function AllyToday({ data, tech, company }: { data?: MyDay; tech?: TechHome; company?: string }) {
  const [d, setD] = useState<MyDay | null>(data || null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (!data) api<MyDay>("/aliado/mi-dia").then(setD).catch((e) => setErr(errorText(e))); }, [data]);
  if (err) return <p className="muted">{err}</p>;
  if (!d) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const vacio = !d.ordenes.length && !d.levantamientos.length && !d.tickets.length;
  const nada = vacio && (!tech || (!tech.proximas.length && !tech.oportunidades.length));
  const quien = tech?.asignan.length ? tech.asignan.join(", ") : company ? `el encargado de ${company}` : "su empresa";
  return (
    <>
      <div className="cp-h"><div><div className="meta">{new Date().toLocaleDateString("es-CR", { weekday: "long", day: "numeric", month: "long", timeZone: "America/Costa_Rica" })}</div><h1 className="h1">Hoy</h1></div></div>
      {nada ? (
        <Empty
          title="Todavía no tiene trabajo asignado"
          hint={`Aquí van a aparecer sus levantamientos, órdenes de trabajo y tickets. Se los asigna ${quien} o Crimson; cuando pase, también le llega un correo. Si espera algo y no lo ve, consúltele a ${quien}.`}
        />
      ) : vacio ? (
        <div className="cp-card"><b>Nada para hoy</b><p className="muted" style={{ margin: "4px 0 0" }}>No tiene órdenes, levantamientos ni tickets pendientes hoy. Abajo están sus próximas órdenes y las oportunidades en común.</p></div>
      ) : <TodayLists d={d} />}
      {tech && <TechExtra t={tech} company={company} />}
    </>
  );
}
