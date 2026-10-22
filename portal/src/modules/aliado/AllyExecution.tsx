/* Ejecución del proyecto en común (reunión 2, pedido 17): qué órdenes de trabajo hay, cuándo y quién va a cada una.
   Las de su equipo se abren; las de Crimson se ven en resumen (de Crimson, solo el nombre de pila). Sin precios,
   costos ni materiales. Lo usan el detalle de la oportunidad y el del proyecto. */
import { Link } from "react-router-dom";
import { fmtDate } from "../../lib/api";
import { Badge } from "../../ui/components";
import { fmtWhen, type Ejecucion, type ExecOrder } from "./shared";

function Row({ o }: { o: ExecOrder }) {
  const body = (
    <>
      <div className="cp-row__main">
        <b>{o.number} · {o.title}</b>
        <div className="cp-row__meta">
          {o.team === "aliado" ? "Su equipo" : "Crimson"} · {o.technicians.length ? o.technicians.join(", ") : "Sin técnico asignado"}
        </div>
        <div className="cp-row__meta">{o.status === "finalizada" && o.finished_at ? `Terminada ${fmtWhen(o.finished_at)}` : o.scheduled_at ? fmtWhen(o.scheduled_at) : "Sin fecha"}</div>
      </div>
      <div className="cp-row__side"><Badge status={o.status} /></div>
    </>
  );
  return o.id ? <Link to={`/ordenes/${o.id}`} className="cp-row">{body}</Link> : <div className="cp-row" title="Orden de Crimson: se ve en resumen">{body}</div>;
}

export default function AllyExecution({ e, showProject }: { e: Ejecucion; showProject?: boolean }) {
  const pr = e.project;
  return (
    <>
      <p className="muted" style={{ margin: "0 0 8px" }}>
        {showProject && <><Link to={`/proyectos/${pr.id}`}>{pr.number} · {pr.name}</Link> · </>}
        {e.total ? `${e.finalizadas} de ${e.total} órdenes terminadas` : "Todavía no hay órdenes de trabajo"}
        {pr.start_date || pr.end_date ? ` · ${pr.start_date ? fmtDate(pr.start_date) : "—"} → ${pr.end_date ? fmtDate(pr.end_date) : "—"}` : ""}
      </p>
      {e.orders.length > 0 && <div className="cp-list">{e.orders.map((o) => <Row key={`${o.team}-${o.number}`} o={o} />)}</div>}
    </>
  );
}
