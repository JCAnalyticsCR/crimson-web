/* Mis equipos: lo que Crimson dejo instalado, con su garantia, y los mantenimientos programados. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../../lib/api";
import type { Asset, Visit } from "./shared";

const ESTADO: Record<string, string> = { activo: "En uso", retirado: "Retirado", garantia: "En garantía", reemplazado: "Reemplazado" };

export default function ClientAssets() {
  const [rows, setRows] = useState<Asset[] | null>(null);
  const [visitas, setVisitas] = useState<Visit[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api<Asset[]>("/cliente/equipos").then(setRows).catch((e) => setErr(e.message));
    api<Visit[]>("/cliente/mantenimientos").then(setVisitas).catch(() => setVisitas([]));
  }, []);
  return (
    <>
      <div className="cp-h"><div><div className="meta">Instalaciones</div><h1 className="h1">Mis equipos</h1></div></div>
      {visitas.length > 0 && (
        <>
          <h2 className="h3" style={{ margin: "0 0 8px" }}>Mantenimientos</h2>
          <div className="cp-list" style={{ marginBottom: 18 }}>
            {visitas.map((v) => (
              <div key={v.id} className="cp-row">
                <div className="cp-row__main"><b>{v.name}</b><div className="cp-row__meta">Cada {v.every_months} meses{v.last_done ? ` · última: ${fmtDate(v.last_done)}` : ""}{v.scope ? ` · ${v.scope}` : ""}</div></div>
                <div className="cp-row__side"><span className="meta">Próxima</span><b>{fmtDate(v.next_date)}</b></div>
              </div>
            ))}
          </div>
        </>
      )}
      <h2 className="h3" style={{ margin: "0 0 8px" }}>Equipos instalados</h2>
      {err ? <p className="muted">{err}</p> : rows === null ? <span className="spinner" /> : rows.length === 0 ? <p className="muted">Todavía no tenemos equipos registrados a su nombre.</p> : (
        <div className="cp-list">
          {rows.map((a) => (
            <div key={a.id} className="cp-row">
              <div className="cp-row__main">
                <b>{a.name}</b>
                <div className="cp-row__meta">{[a.model, a.serial && `Serie ${a.serial}`, a.location, a.installed_at && `Instalado ${fmtDate(a.installed_at)}`].filter(Boolean).join(" · ")}</div>
              </div>
              <div className="cp-row__side">
                {a.warranty_until ? <span className={`badge badge--${a.in_warranty ? "ok" : "muted"}`}>{a.in_warranty ? `Garantía hasta ${fmtDate(a.warranty_until)}` : "Sin garantía"}</span> : <span className="meta">{ESTADO[a.status] || a.status}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
      <p className="meta" style={{ marginTop: 14 }}>¿Un equipo falla? <Link to="/mis-tickets/nuevo">Repórtelo aquí</Link>.</p>
    </>
  );
}
