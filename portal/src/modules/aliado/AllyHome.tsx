/* Inicio del aliado (Andrés, 21/10): primero lo que va a ganar, después los proyectos y oportunidades en común y los
   levantamientos que puede hacer; luego precios que Crimson le pidió, tickets y el trabajo de hoy de su equipo.
   El técnico ve "Hoy" con sus próximas órdenes y las oportunidades en común (reunión 2, pedido 21). */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import AllyToday from "./AllyToday";
import { ESTADO_PRECIO, errorText, fmtWhen, useAlly, type MyDay, type OrderRow, type Req, type TechHome } from "./shared";
import { Badge } from "../../ui/components";

type Inicio = {
  company: { name: string; state: string; suspended_reason: string | null }; role: string; preview: boolean;
  counters?: Record<string, number> & { precio_mas_proximo?: string | null };
  precios_pedidos?: Req[]; hoy?: OrderRow[] | MyDay; tecnico?: TechHome; dinero?: { recibido: string; por_recibir: string; proyectado: string; moneda: string } | null;
};

export default function AllyHome() {
  const { isTech, can } = useAlly();
  const [d, setD] = useState<Inicio | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Inicio>("/aliado/inicio").then(setD).catch((e) => setErr(errorText(e))); }, []);
  if (err) return <p className="muted">{err}</p>;
  if (!d) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  if (isTech) return <AllyToday data={d.hoy as MyDay | undefined} tech={d.tecnico} company={d.company.name} />;
  const c = d.counters;
  if (!c) {
    return (
      <div className="cp-card">
        <h1 className="h2" style={{ marginTop: 0 }}>{d.company.name}</h1>
        <p>El acceso está suspendido{d.company.suspended_reason ? `: ${d.company.suspended_reason}` : "."}</p>
        {can("suscripcion") && <Link className="btn btn--crimson" to="/suscripcion">Ver mi suscripción y facturas</Link>}
      </div>
    );
  }
  const hoy = (d.hoy as OrderRow[]) || [];
  const stat = (to: string, label: string, n: number, sub?: string) => (
    <Link to={to} className="cp-stat" style={{ textDecoration: "none", color: "inherit" }}><span className="meta">{label}</span><b>{n}</b>{sub && <span className="muted" style={{ fontSize: 13 }}>{sub}</span>}</Link>
  );
  return (
    <>
      <div className="cp-h"><div><div className="meta">{d.company.name}</div><h1 className="h1">¡Hola!</h1></div></div>
      {d.dinero ? (
        <div className="cp-card al-money" style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <div className="meta">Lo que gana con la alianza · sin IVA</div>
            {can("ganancias") && <Link className="btn btn--soft btn--sm" to="/ganancias">Ver mis ganancias por proyecto</Link>}
          </div>
          <div className="cp-grid cp-grid--3" style={{ marginTop: 8 }}>
            <div><span className="meta">Recibido</span><b style={{ display: "block", fontSize: 20 }}>{fmtMoney(d.dinero.recibido, d.dinero.moneda)}</b></div>
            <div><span className="meta">Por recibir</span><b style={{ display: "block", fontSize: 20 }}>{fmtMoney(d.dinero.por_recibir, d.dinero.moneda)}</b></div>
            <div><span className="meta">Proyectado</span><b style={{ display: "block", fontSize: 20 }}>{fmtMoney(d.dinero.proyectado, d.dinero.moneda)}</b></div>
          </div>
        </div>
      ) : can("ganancias") && (
        <div className="cp-card" style={{ marginBottom: 16 }}>
          <div className="meta">Lo que gana con la alianza</div>
          <p className="muted" style={{ margin: "6px 0 0" }}>Aparece aquí cuando Crimson registre el acuerdo de un proyecto en común. <Link to="/ganancias">Mis ganancias</Link></p>
        </div>
      )}
      <div className="cp-grid cp-grid--3" style={{ marginBottom: 16 }}>
        {stat("/proyectos", "Proyectos activos en común", c.proyectos_activos)}
        {stat("/oportunidades", "Oportunidades abiertas en común", c.oportunidades_abiertas)}
        {stat("/levantamientos", "Levantamientos por hacer", c.levantamientos_por_hacer ?? 0, c.levantamientos_devueltos ? `${c.levantamientos_devueltos} devuelto(s) para corregir` : undefined)}
      </div>
      <div className="cp-grid cp-grid--3" style={{ marginBottom: 16 }}>
        {stat("/oportunidades", "Precios que Crimson le pidió", c.precios_pendientes, c.precio_mas_proximo ? `Antes del ${fmtDate(c.precio_mas_proximo)}` : undefined)}
        {stat("/ordenes", "Órdenes de hoy (su equipo)", c.ot_hoy_equipo)}
        {stat("/soporte", "Tickets abiertos", c.tickets_abiertos, c.tickets_fuera_de_tiempo ? `${c.tickets_fuera_de_tiempo} fuera de tiempo` : undefined)}
        {stat("/mantenimientos", "Mantenimientos en 30 días", c.mantenimientos_30_dias)}
      </div>
      <h2 className="h3 al-sec">Precios que Crimson le pidió</h2>
      {d.precios_pedidos?.length ? (
        <div className="cp-list">
          {d.precios_pedidos.map((r) => (
            <Link key={r.id} to={`/oportunidades/${r.opportunity_id}`} className="cp-row">
              <div className="cp-row__main"><b>{r.what}</b><div className="cp-row__meta">{r.due_date ? `Antes del ${fmtDate(r.due_date)}` : "Sin fecha límite"}</div></div>
              <div className="cp-row__side"><span className={`badge badge--${ESTADO_PRECIO[r.status]?.tone || "muted"}`}>{ESTADO_PRECIO[r.status]?.label || r.status}</span><span className="btn btn--soft btn--sm">Responder</span></div>
            </Link>
          ))}
        </div>
      ) : <p className="muted">No hay precios pendientes.</p>}
      <h2 className="h3 al-sec">Hoy de mi equipo</h2>
      {hoy.length ? (
        <div className="cp-list">
          {hoy.map((o) => (
            <Link key={o.id} to={`/ordenes/${o.id}`} className="cp-row">
              <div className="cp-row__main"><b>{o.number} · {o.title}</b><div className="cp-row__meta">{o.technician || "Sin técnico"} · {o.scheduled_at ? fmtWhen(o.scheduled_at) : "Sin fecha"}</div></div>
              <div className="cp-row__side"><Badge status={o.status} /></div>
            </Link>
          ))}
        </div>
      ) : <p className="muted">Su equipo no tiene órdenes para hoy.</p>}
    </>
  );
}
