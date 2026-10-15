/* Soporte del aliado (15:48-16:54): los tickets asignados a su empresa con cliente, tipo, prioridad, responsable y
   vencimiento (fuera de tiempo resaltado). El técnico ve los que le asignaron. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { Empty, I, Icon } from "../../ui/components";
import { ESTADO_TICKET, PRIORIDAD, TIPO_TICKET, errorText, fmtWhen, useAlly, type TicketRow } from "./shared";

export default function AllyTickets() {
  const { can, readOnly } = useAlly();
  const [estado, setEstado] = useState<"abiertos" | "todos">("abiertos");
  const [rows, setRows] = useState<TicketRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setRows(null); api<TicketRow[]>(`/aliado/tickets?estado=${estado}`).then(setRows).catch((e) => setErr(errorText(e))); }, [estado]);
  return (
    <>
      <div className="cp-h">
        <h1 className="h1">Soporte</h1>
        {can("tickets_crear") && !readOnly && <Link className="btn btn--crimson" to="/soporte/nuevo"><Icon d={I.plus} />Abrir ticket</Link>}
      </div>
      <div className="cp-seg">
        <button className={estado === "abiertos" ? "is-on" : ""} onClick={() => setEstado("abiertos")}>Abiertos</button>
        <button className={estado === "todos" ? "is-on" : ""} onClick={() => setEstado("todos")}>Todos</button>
      </div>
      {err && <p className="muted">{err}</p>}
      {!rows ? <div className="spinner" /> : rows.length ? (
        <div className="cp-list">
          {rows.map((t) => (
            <Link key={t.id} to={`/soporte/${t.id}`} className="cp-row" style={t.fuera_de_tiempo ? { borderColor: "var(--bad)" } : undefined}>
              <div className="cp-row__main">
                <b>{t.number} · {t.subject}</b>
                <div className="cp-row__meta">{t.customer || "—"}{t.project ? ` · ${t.project}` : ""} · {TIPO_TICKET[t.kind] || t.kind} · prioridad {PRIORIDAD[t.priority] || t.priority}</div>
                <div className="cp-row__meta">{t.assigned || "Sin responsable"}{t.resolve_due_at ? ` · resolver antes de ${fmtWhen(t.resolve_due_at)}` : ""}</div>
              </div>
              <div className="cp-row__side">
                <span className={`badge badge--${t.fuera_de_tiempo ? "bad" : t.status === "resuelto" ? "ok" : "info"}`}>{ESTADO_TICKET[t.status] || t.status}</span>
                {t.fuera_de_tiempo && <span className="meta" style={{ color: "var(--bad)" }}>Fuera de tiempo</span>}
              </div>
            </Link>
          ))}
        </div>
      ) : <Empty title="No hay tickets aquí" hint="Los tickets que Crimson asigna a su empresa aparecen en esta lista." />}
    </>
  );
}
