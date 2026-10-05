/* Inicio del cliente: lo que tiene abierto con Crimson y cuando es la proxima visita. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../../lib/api";
import { I, Icon } from "../../ui/components";
import { TicketList } from "./ClientTickets";
import type { Home } from "./shared";

export default function ClientHome() {
  const [d, setD] = useState<Home | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Home>("/cliente/inicio").then(setD).catch((e) => setErr(e.message)); }, []);
  if (err) return <p className="muted">{err}</p>;
  if (!d) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const v = d.next_visit;
  return (
    <>
      <div className="cp-h">
        <div><div className="meta">{d.customer.name}</div><h1 className="h1">¡Hola!</h1></div>
        <Link className="btn btn--crimson" to="/mis-tickets/nuevo"><Icon d={I.plus} />Reportar una falla</Link>
      </div>
      <div className="cp-grid cp-grid--3" style={{ marginBottom: 16 }}>
        <Link to="/mis-tickets" className="cp-stat" style={{ textDecoration: "none", color: "inherit" }}><span className="meta">Tickets abiertos</span><b>{d.open_count}</b></Link>
        <div className="cp-stat">
          <span className="meta">Próxima visita de mantenimiento</span>
          <b style={{ fontSize: 20 }}>{v?.next_date ? fmtDate(v.next_date) : "Sin programar"}</b>
          {v && <span className="muted" style={{ fontSize: 13 }}>{v.name}{v.every_months ? ` · cada ${v.every_months} meses` : ""}</span>}
        </div>
        <Link to="/mis-equipos" className="cp-stat" style={{ textDecoration: "none", color: "inherit" }}><span className="meta">Equipos instalados</span><b>{d.assets_count}</b></Link>
      </div>
      <h2 className="h3" style={{ margin: "8px 0" }}>Lo que tiene abierto</h2>
      {d.open_tickets.length ? <TicketList rows={d.open_tickets} /> : <p className="muted">No tiene tickets abiertos. Si algo falla, repórtelo y aquí puede seguir cada avance.</p>}
    </>
  );
}
