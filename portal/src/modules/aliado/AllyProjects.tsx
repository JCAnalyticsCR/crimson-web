/* Proyectos en común (cotización aprobada por el cliente): estado, fechas y avance de las órdenes de SU equipo. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../../lib/api";
import { Badge, Empty } from "../../ui/components";
import { ROL_PARTICIPACION, errorText, type ProjectRow } from "./shared";

export default function AllyProjects() {
  const [estado, setEstado] = useState<"activos" | "todos">("activos");
  const [rows, setRows] = useState<ProjectRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setRows(null); api<ProjectRow[]>(`/aliado/proyectos?estado=${estado}`).then(setRows).catch((e) => setErr(errorText(e))); }, [estado]);
  return (
    <>
      <div className="cp-h"><h1 className="h1">Proyectos</h1></div>
      <div className="cp-seg">
        <button className={estado === "activos" ? "is-on" : ""} onClick={() => setEstado("activos")}>Activos</button>
        <button className={estado === "todos" ? "is-on" : ""} onClick={() => setEstado("todos")}>Todos</button>
      </div>
      {err && <p className="muted">{err}</p>}
      {!rows ? <div className="spinner" /> : rows.length ? (
        <div className="cp-list">
          {rows.map((p) => (
            <Link key={p.id} to={`/proyectos/${p.id}`} className="cp-row">
              <div className="cp-row__main">
                <b>{p.number} · {p.name}</b>
                <div className="cp-row__meta">{p.customer || "—"}{p.end_customer ? ` → ${p.end_customer}` : ""}{p.site ? ` · ${p.site}` : ""}</div>
                <div className="cp-row__meta">{p.my_relations?.length ? p.my_relations.join(", ") : `Su papel: ${p.my_roles.map((r) => ROL_PARTICIPACION[r] || r).join(", ")}`}{p.start_date ? ` · inicio ${fmtDate(p.start_date)}` : ""}</div>
              </div>
              <div className="cp-row__side"><Badge status={p.status} /><span className="meta">{p.orders_done}/{p.orders_total} órdenes</span></div>
            </Link>
          ))}
        </div>
      ) : <Empty title="Aún no hay proyectos en común con Crimson" />}
    </>
  );
}
