import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney, type Dashboard as D } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Empty, I, Icon, Spark } from "../../ui/components";

function Kpi({ label, data, light, cur }: { label: string; data: D["pagos"]; light?: boolean; cur: string }) {
  const v = data.variacion;
  return (
    <div className={`kpi__card ${light ? "kpi__card--light" : ""}`}>
      <span className="kpi__corner" />
      <div className="kpi__label">{label} · este mes</div>
      <div className="kpi__value">{fmtMoney(data.mes, cur)}</div>
      <div className="kpi__sub">
        <span>Hoy <b className="money">{fmtMoney(data.hoy, cur)}</b></span>
        <span>Mes anterior <b className="money">{fmtMoney(data.mes_anterior, cur)}</b></span>
        {v !== null && <span className={`kpi__delta ${v >= 0 ? "kpi__delta--up" : "kpi__delta--down"}`}>{v >= 0 ? "▲" : "▼"} {Math.abs(v).toFixed(1)}%</span>}
      </div>
      <Spark values={[Number(data.mes_anterior), Number(data.mes_anterior) * 0.8, Number(data.mes) * 0.6, Number(data.mes)]} color={light ? "#e2233a" : "#ff5a6e"} />
    </div>
  );
}

export default function Dashboard() {
  const { me } = useSession();
  const [d, setD] = useState<D | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => api<D>("/dashboard").then(setD).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const cur = me?.tenant.default_currency || "CRC";
  const hour = new Date().getHours();
  const saludo = hour < 12 ? "Buenos días" : hour < 18 ? "Buenas tardes" : "Buenas noches";

  return (
    <>
      <div className="page-head">
        <div>
          <div className="meta">01 · Inicio</div>
          <h1 className="h1">{saludo}, {me?.user.full_name.split(" ")[0]}.</h1>
        </div>
        <div className="page-head__actions">
          <button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} />Refrescar</button>
        </div>
      </div>

      <div className="actions-grid">
        <Link className="action" to="/clientes?nuevo=1"><i><Icon d={I.customers} /></i><div><b>Crear cliente</b><span>Ficha con cédula, correo y WhatsApp</span></div></Link>
        <Link className="action" to="/productos?nuevo=1"><i><Icon d={I.products} /></i><div><b>Crear producto</b><span>Precio, CABYS e impuesto</span></div></Link>
        <Link className="action" to="/cotizaciones/nueva"><i><Icon d={I.quote} /></i><div><b>Crear cotización</b><span>Y convertirla a factura en un clic</span></div></Link>
      </div>

      {err && <p style={{ color: "var(--bad)" }}>{err}</p>}
      {d && (
        <>
          <div className="kpi">
            <Kpi label="Pagos" data={d.pagos} cur={cur} />
            <Kpi label="Facturado" data={d.facturado} cur={cur} light />
          </div>

          <Card title="Acciones pendientes" extra={<span className="meta">se actualiza en vivo</span>}>
            <div className="pending">
              <div className={d.acciones_pendientes.facturas_vencidas ? "hot" : ""}><b>{d.acciones_pendientes.facturas_vencidas}</b><span>Facturas vencidas</span></div>
              <div><b>{d.acciones_pendientes.facturas_por_cobrar}</b><span>Facturas por cobrar</span></div>
              <div><b>{d.acciones_pendientes.cotizaciones_sin_respuesta}</b><span>Cotizaciones sin respuesta</span></div>
              <div><b>{d.acciones_pendientes.enlaces_abiertos}</b><span>Enlaces de pago abiertos</span></div>
              <div className={d.acciones_pendientes.documentos_rechazados ? "hot" : ""}><b>{d.acciones_pendientes.documentos_rechazados}</b><span>Rechazados por Hacienda</span></div>
              <div><b>{d.acciones_pendientes.stock_bajo}</b><span>Productos con stock bajo</span></div>
            </div>
          </Card>

          <div className="grid-2">
            <Card title="Pagos recientes" flush extra={<Link className="btn btn--ghost btn--sm" to="/pagos">Ver todos</Link>}>
              {d.pagos_recientes.length === 0 ? <Empty hint="Los pagos registrados en facturas aparecen aquí." /> : (
                <table className="table">
                  <thead><tr><th>Referencia</th><th>Método</th><th>Fecha</th><th className="num">Monto</th><th>Estado</th></tr></thead>
                  <tbody>{d.pagos_recientes.map((p) => (
                    <tr key={p.id}><td><Link className="row-link" to={`/facturas/${p.invoice_id}`}>{p.ref || `#${p.id}`}</Link></td><td className="muted" style={{ textTransform: "capitalize" }}>{p.method}</td><td className="muted">{fmtDate(p.date)}</td><td className="num money">{fmtMoney(p.amount, p.currency)}</td><td><Badge status={p.status} /></td></tr>
                  ))}</tbody>
                </table>
              )}
            </Card>
            <Card title="Facturas recientes" flush extra={<Link className="btn btn--ghost btn--sm" to="/facturas">Ver todas</Link>}>
              {d.facturas_recientes.length === 0 ? <Empty hint="Creá tu primera factura desde una cotización." action={<Link className="btn btn--crimson btn--sm" to="/cotizaciones/nueva">Nueva cotización</Link>} /> : (
                <table className="table">
                  <thead><tr><th>Código</th><th>Cliente</th><th className="num">Importe</th><th>Estado</th></tr></thead>
                  <tbody>{d.facturas_recientes.map((f) => (
                    <tr key={f.id}><td><Link className="row-link mono" to={`/facturas/${f.id}`}>{f.number}</Link></td><td>{f.customer || "—"}</td><td className="num money">{fmtMoney(f.total, f.currency)}</td><td><Badge status={f.status} /></td></tr>
                  ))}</tbody>
                </table>
              )}
            </Card>
          </div>
        </>
      )}
    </>
  );
}
