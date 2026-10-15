/* Mantenimientos (17:18-18:06): contratos asignados a su empresa o de lo que instalaron en común con Crimson.
   Próxima visita, cada cuántos meses, alcance y tickets abiertos; sin montos. El estado de los equipos por la API de
   video queda marcado como pendiente de conexión (la cuenta de desarrollo está bloqueada). */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../../lib/api";
import { Empty } from "../../ui/components";
import { ESTADO_TICKET, errorText } from "./shared";

type Contract = {
  id: number; number: string; name: string; kind: string; customer: string | null; project: string | null; every_months: number; next_date: string | null; last_done: string | null;
  dias_para_la_proxima: number | null; scope: string | null; active: boolean; tickets_abiertos: { id: number; number: string; status: string; assigned: string | null }[]; device_status_label: string;
};

export default function AllyContracts() {
  const [rows, setRows] = useState<Contract[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Contract[]>("/aliado/mantenimientos").then(setRows).catch((e) => setErr(errorText(e))); }, []);
  return (
    <>
      <div className="cp-h"><h1 className="h1">Mantenimientos</h1></div>
      {err && <p className="muted">{err}</p>}
      {!rows ? <div className="spinner" /> : rows.length ? (
        <div className="cp-list">
          {rows.map((c) => (
            <div key={c.id} className="cp-row">
              <div className="cp-row__main">
                <b>{c.number} · {c.name}</b>
                <div className="cp-row__meta">{c.customer || "—"}{c.project ? ` · ${c.project}` : ""} · cada {c.every_months} meses{c.last_done ? ` · última ${fmtDate(c.last_done)}` : ""}</div>
                {c.scope && <div className="cp-row__meta" style={{ whiteSpace: "pre-wrap" }}>{c.scope}</div>}
                {c.tickets_abiertos.map((t) => <div key={t.id} className="cp-row__meta"><Link to={`/soporte/${t.id}`}>{t.number}</Link> · {ESTADO_TICKET[t.status] || t.status}{t.assigned ? ` · ${t.assigned}` : ""}</div>)}
                <div className="meta">{c.device_status_label}</div>
              </div>
              <div className="cp-row__side">
                <b>{c.next_date ? fmtDate(c.next_date) : "Sin programar"}</b>
                {c.dias_para_la_proxima !== null && <span className={`badge badge--${c.dias_para_la_proxima < 0 ? "bad" : c.dias_para_la_proxima <= 30 ? "warn" : "muted"}`}>{c.dias_para_la_proxima < 0 ? "Atrasado" : `en ${c.dias_para_la_proxima} días`}</span>}
              </div>
            </div>
          ))}
        </div>
      ) : <Empty title="Aún no hay mantenimientos en común" />}
    </>
  );
}
