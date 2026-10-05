/* Mis tickets: los de toda la empresa (los ajenos se ven, pero solo escribe quien lo abrio o el encargado). */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { I, Icon } from "../../ui/components";
import { ESTADO, PRIORIDAD, fmtWhen, type TicketRow } from "./shared";

export function EstadoBadge({ status }: { status: string }) {
  const s = ESTADO[status] ?? { label: status, tone: "muted" as const };
  return <span className={`badge badge--${s.tone}`}>{s.label}</span>;
}

export function TicketList({ rows }: { rows: TicketRow[] }) {
  return (
    <div className="cp-list">
      {rows.map((t) => (
        <Link key={t.id} to={`/mis-tickets/${t.id}`} className="cp-row">
          <div className="cp-row__main">
            <b>{t.subject}</b>
            <div className="cp-row__meta">{t.number} · {fmtWhen(t.created_at)}{t.asset ? ` · ${t.asset}` : ""}{t.origin === "empresa" ? " · lo abrió otra persona de su empresa" : t.origin === "crimson" ? " · registrado por Crimson" : ""}</div>
          </div>
          <div className="cp-row__side">
            <EstadoBadge status={t.status} />
            <span className="meta">{PRIORIDAD[t.priority] || t.priority}</span>
          </div>
        </Link>
      ))}
    </div>
  );
}

const FILTROS = [{ k: "abiertos", l: "Abiertos" }, { k: "cerrados", l: "Resueltos y cerrados" }, { k: "todos", l: "Todos" }];

export default function ClientTickets() {
  const [estado, setEstado] = useState("abiertos");
  const [rows, setRows] = useState<TicketRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setRows(null); api<TicketRow[]>(`/cliente/tickets?estado=${estado}`).then(setRows).catch((e) => setErr(e.message)); }, [estado]);
  return (
    <>
      <div className="cp-h">
        <div><div className="meta">Soporte</div><h1 className="h1">Mis tickets</h1></div>
        <Link className="btn btn--crimson" to="/mis-tickets/nuevo"><Icon d={I.plus} />Reportar una falla</Link>
      </div>
      <div className="cp-seg" role="tablist">
        {FILTROS.map((f) => <button key={f.k} role="tab" aria-selected={estado === f.k} className={estado === f.k ? "is-on" : ""} onClick={() => setEstado(f.k)}>{f.l}</button>)}
      </div>
      {err ? <p className="muted">{err}</p> : rows === null ? <span className="spinner" /> : rows.length ? <TicketList rows={rows} /> : <p className="muted">No hay tickets en esta lista.</p>}
    </>
  );
}
