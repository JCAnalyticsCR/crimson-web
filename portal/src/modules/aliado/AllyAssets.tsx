/* Activos del cliente en común (18:14-18:38, "todos no"): equipos y garantía, búsqueda por serie y filtro por
   cliente en común. Sin IP, MAC, firmware ni datos de compra. */
import { useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { Empty } from "../../ui/components";
import { errorText } from "./shared";

type Asset = { id: number; name: string; model: string | null; serial: string | null; location: string | null; installed_at: string | null; warranty_until: string | null; warranty_days: number | null; status: string; customer: string | null; project: string | null };

export default function AllyAssets() {
  const [clientes, setClientes] = useState<{ id: number; name: string; proyectos: number }[]>([]);
  const [cid, setCid] = useState("");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<Asset[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<{ id: number; name: string; proyectos: number }[]>("/aliado/clientes").then(setClientes).catch(() => setClientes([])); }, []);
  useEffect(() => {
    const t = setTimeout(() => api<Asset[]>(`/aliado/activos?q=${encodeURIComponent(q)}${cid ? `&customer_id=${cid}` : ""}`).then(setRows).catch((e) => setErr(errorText(e))), 200);
    return () => clearTimeout(t);
  }, [q, cid]);
  return (
    <>
      <div className="cp-h"><h1 className="h1">Activos del cliente</h1></div>
      <div className="cp-grid cp-grid--2" style={{ marginBottom: 12 }}>
        <select className="select" value={cid} onChange={(e) => setCid(e.target.value)}>
          <option value="">Todos los clientes en común</option>
          {clientes.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <input className="input" placeholder="Buscar por nombre, modelo o serie" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {err && <p className="muted">{err}</p>}
      {!rows ? <div className="spinner" /> : rows.length ? (
        <div className="al-table">
          <table>
            <thead><tr><th>Equipo</th><th>Serie</th><th>Ubicación</th><th>Cliente</th><th>Garantía</th></tr></thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id}>
                  <td><b>{a.name}</b>{a.model ? <div className="meta">{a.model}</div> : null}</td>
                  <td>{a.serial || "—"}</td>
                  <td>{a.location || "—"}</td>
                  <td>{a.customer || "—"}{a.project ? <div className="meta">{a.project}</div> : null}</td>
                  <td>{a.warranty_until ? <span className={`badge badge--${(a.warranty_days ?? 0) < 0 ? "muted" : (a.warranty_days ?? 0) <= 30 ? "warn" : "ok"}`}>{fmtDate(a.warranty_until)}</span> : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <Empty title="No hay equipos en común" />}
    </>
  );
}
