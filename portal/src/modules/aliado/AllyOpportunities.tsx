/* Oportunidades en común (14:20-14:31): solo las de su empresa, sin montos ni probabilidad de Crimson. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { Empty } from "../../ui/components";
import { ESTADO_OPP, ROL_PARTICIPACION, errorText, type OppRow } from "./shared";

export default function AllyOpportunities() {
  const [estado, setEstado] = useState<"abiertas" | "cerradas" | "todas">("abiertas");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<OppRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const t = setTimeout(() => api<OppRow[]>(`/aliado/oportunidades?estado=${estado}&q=${encodeURIComponent(q)}`).then(setRows).catch((e) => setErr(errorText(e))), 200);
    return () => clearTimeout(t);
  }, [estado, q]);
  return (
    <>
      <div className="cp-h"><h1 className="h1">Oportunidades</h1></div>
      <div className="cp-seg">
        {(["abiertas", "cerradas", "todas"] as const).map((k) => <button key={k} className={estado === k ? "is-on" : ""} onClick={() => setEstado(k)}>{k === "abiertas" ? "Abiertas" : k === "cerradas" ? "Cerradas" : "Todas"}</button>)}
      </div>
      <input className="input" style={{ width: "100%", marginBottom: 12 }} placeholder="Buscar por título, número o sitio" value={q} onChange={(e) => setQ(e.target.value)} />
      {err && <p className="muted">{err}</p>}
      {!rows ? <div className="spinner" /> : rows.length ? (
        <div className="cp-list">
          {rows.map((o) => (
            <Link key={o.id} to={`/oportunidades/${o.id}`} className="cp-row">
              <div className="cp-row__main">
                <b>{o.number} · {o.title}</b>
                <div className="cp-row__meta">{o.customer || "—"}{o.end_customer ? ` → ${o.end_customer}` : ""}{o.site ? ` · ${o.site}` : ""}</div>
                <div className="cp-row__meta">Su papel: {o.my_roles.map((r) => ROL_PARTICIPACION[r] || r).join(", ")}{o.crimson_owner ? ` · En Crimson: ${o.crimson_owner}` : ""}</div>
              </div>
              <div className="cp-row__side">
                <span className="badge badge--info">{ESTADO_OPP[o.status] || o.status}</span>
                {o.pending_requests > 0 && <span className="badge badge--warn">{o.pending_requests} precio(s) pedido(s)</span>}
              </div>
            </Link>
          ))}
        </div>
      ) : <Empty title="Aún no hay oportunidades en común con Crimson" hint="Cuando Crimson lo sume a una oportunidad, aparece aquí." />}
    </>
  );
}
