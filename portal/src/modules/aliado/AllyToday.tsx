/* "Hoy" (26:03-26:31): lo que le toca hacer al técnico del aliado hoy, en tarjetas grandes para el celular.
   Órdenes de hoy y en curso (las sin fecha dicen "Sin fecha"), levantamientos asignados en borrador o devueltos y
   tickets asignados. Sin precios en ningún lado. El aliado ve lo mismo para todo su equipo (con quién lo hace). */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { Badge, Empty } from "../../ui/components";
import { ESTADO_TICKET, PRIORIDAD, errorText, fmtWhen, type MyDay } from "./shared";

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

export default function AllyToday({ data }: { data?: MyDay }) {
  const [d, setD] = useState<MyDay | null>(data || null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (!data) api<MyDay>("/aliado/mi-dia").then(setD).catch((e) => setErr(errorText(e))); }, [data]);
  if (err) return <p className="muted">{err}</p>;
  if (!d) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const vacio = !d.ordenes.length && !d.levantamientos.length && !d.tickets.length;
  return (
    <>
      <div className="cp-h"><div><div className="meta">{new Date().toLocaleDateString("es-CR", { weekday: "long", day: "numeric", month: "long", timeZone: "America/Costa_Rica" })}</div><h1 className="h1">Hoy</h1></div></div>
      {vacio ? <Empty title="Nada asignado para hoy" hint="Cuando su empresa o Crimson le asigne un trabajo, aparece aquí." /> : <TodayLists d={d} />}
    </>
  );
}
