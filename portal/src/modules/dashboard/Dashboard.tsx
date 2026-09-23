import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney, parseTs, type Dashboard as D } from "../../lib/api";
import { useSession } from "../../app/session";
import { roleMeta } from "../../app/roles";
import { Badge, Card, Empty, I, Icon, Spark } from "../../ui/components";

function Kpi({ label, data, light, cur }: { label: string; data: NonNullable<D["pagos"]>; light?: boolean; cur: string }) {
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

type Job = { id: number; number: string; title: string; status: string; customer: string | null; site: string | null; scheduled_at: string | null };

const JobRow = ({ o }: { o: Job }) => (
  <Link className="job" to={`/ordenes-trabajo?id=${o.id}`}>
    <span className="job__when">{o.scheduled_at ? parseTs(o.scheduled_at).toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit", timeZone: "America/Costa_Rica" }) : "—"}</span>
    <span style={{ flex: 1, minWidth: 0 }}><b>{o.title}</b><small>{o.customer || "Sin cliente"}{o.site ? ` · ${o.site}` : ""}</small></span>
    <Badge status={o.status} />
  </Link>
);

export default function Dashboard() {
  const { me, allows, role } = useSession();
  const rm = roleMeta(role);
  const [d, setD] = useState<D | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [low, setLow] = useState<{ product_id: number; name: string; code: string; quantity: string; min_stock: number }[] | null>(null);
  const [day, setDay] = useState<{ today: Job[]; next: Job[]; surveys: { id: number; number: string; kind_label: string; site: string | null }[] } | null>(null);
  const salesHome = allows("sales.ver");
  const fieldHome = !salesHome && allows("field.ver"); // el técnico entra a ver su día, no el inventario
  const load = () => {
    if (salesHome) api<D>("/dashboard").then(setD).catch((e) => setErr(e.message));
    else if (fieldHome) api<typeof day>("/work-orders/meta/today").then(setDay).catch((e) => setErr(e.message));
    else api<{ low_stock: typeof low }>("/alerts").then((r) => setLow(r.low_stock || [])).catch((e) => setErr(e.message));
  };
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
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6, flexWrap: "wrap" }}>
            <span className="badge" style={{ background: `color-mix(in srgb, ${rm.tone} 14%, transparent)`, color: rm.tone, fontWeight: 700 }}>{rm.label}</span>
            {d?.scope === "mine" && <span className="muted" style={{ fontSize: 13 }}>Estás viendo tus propias ventas y cobros.</span>}
          </div>
        </div>
        <div className="page-head__actions">
          <button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} />Refrescar</button>
        </div>
      </div>

      <div className="actions-grid">
        {allows("crm.crear") && <Link className="action" to="/clientes?nuevo=1"><i><Icon d={I.customers} /></i><div><b>Crear cliente</b><span>Ficha con cédula, correo y WhatsApp</span></div></Link>}
        {allows("catalog.crear") && <Link className="action" to="/productos?nuevo=1"><i><Icon d={I.products} /></i><div><b>Crear producto</b><span>Precio, CABYS e impuesto</span></div></Link>}
        {allows("sales.crear") && <Link className="action" to="/cotizaciones/nueva"><i><Icon d={I.quote} /></i><div><b>Crear cotización</b><span>Y convertirla a factura en un clic</span></div></Link>}
        {allows("sales.crear") && !allows("catalog.crear") && <Link className="action" to="/pos"><i><Icon d={I.wallet} /></i><div><b>Punto de venta</b><span>Cobro de mostrador con vuelto</span></div></Link>}
        {allows("inventory.crear") && <Link className="action" to="/inventario"><i><Icon d={I.inventory} /></i><div><b>Movimiento de inventario</b><span>Entradas, salidas y transferencias</span></div></Link>}
        {allows("reports.ver") && <Link className="action" to="/reportes"><i><Icon d={I.reports} /></i><div><b>Reportes</b><span>Los reportes de tu rol, con Excel</span></div></Link>}
      </div>

      {err && <p style={{ color: "var(--bad)" }}>{err}</p>}
      {fieldHome && (
        <>
          <Card title={`Hoy · ${day?.today.length ?? 0}`} extra={<Link className="btn btn--ghost btn--sm" to="/ordenes-trabajo">Ver todas</Link>}>
            {!day ? <span className="spinner" /> : day.today.length === 0 ? <p className="muted" style={{ fontSize: 13.5, margin: 0 }}>No hay trabajos programados para hoy.</p> : day.today.map((o) => <JobRow key={o.id} o={o} />)}
          </Card>
          {!!day?.next.length && <Card title={`Próximos · ${day.next.length}`}>{day.next.map((o) => <JobRow key={o.id} o={o} />)}</Card>}
          {!!day?.surveys.length && (
            <Card title="Levantamientos sin enviar">
              {day.surveys.map((s) => <Link className="job" key={s.id} to={`/levantamientos?id=${s.id}`}><span style={{ flex: 1 }}><b>{s.number} · {s.kind_label}</b><small>{s.site || "Sin sitio"}</small></span><span className="badge badge--warn">Borrador</span></Link>)}
            </Card>
          )}
        </>
      )}
      {!salesHome && !fieldHome && (
        <Card title="Existencias bajo el mínimo" flush extra={<Link className="btn btn--ghost btn--sm" to="/inventario">Ir a inventarios</Link>}>
          {low === null ? <div style={{ padding: 24, textAlign: "center" }}><span className="spinner" /></div> : low.length === 0 ? <Empty title="Todo en orden" hint="Ningún producto está por debajo de su stock mínimo." /> : (
            <table className="table"><thead><tr><th>Código</th><th>Producto</th><th className="num">Existencia</th><th className="num">Mínimo</th></tr></thead>
              <tbody>{low.map((x) => <tr key={x.product_id}><td className="mono muted">{x.code}</td><td style={{ fontWeight: 600 }}>{x.name}</td><td className="num mono" style={{ color: "var(--bad)" }}>{Number(x.quantity)}</td><td className="num mono">{x.min_stock}</td></tr>)}</tbody></table>
          )}
        </Card>
      )}
      {d && (
        <>
          <div className="kpi">
            {d.pagos && <Kpi label={d.scope === "mine" ? "Mis cobros" : "Pagos"} data={d.pagos} cur={cur} />}
            <Kpi label={d.scope === "mine" ? "Mi facturación" : "Facturado"} data={d.facturado} cur={cur} light />
          </div>

          {d.gerencia && (
            <Card title="Cómo va Crimson" extra={<span className="meta">pipeline, obra y utilidad</span>}>
              <div className="eco">
                <Link className="eco__box" to="/oportunidades"><span className="meta">Embudo · {d.gerencia.opportunities} oportunidades</span><b className="money">{fmtMoney(d.gerencia.pipeline, cur)}</b></Link>
                <div className="eco__box"><span className="meta">Ponderado por probabilidad</span><b className="money">{fmtMoney(d.gerencia.pipeline_weighted, cur)}</b></div>
                <div className="eco__box"><span className="meta">Por cobrar</span><b className="money">{fmtMoney(d.gerencia.receivable, cur)}</b></div>
                <div className={`eco__box ${Number(d.gerencia.profit_month) >= 0 ? "is-good" : "is-bad"}`}><span className="meta">Utilidad del mes · margen {d.gerencia.margin_month.toFixed(1)}%</span><b className="money">{fmtMoney(d.gerencia.profit_month, cur)}</b></div>
                <Link className="eco__box" to="/proyectos"><span className="meta">Proyectos activos · {d.gerencia.projects_closed_month} cerrados este mes</span><b>{d.gerencia.projects_active}</b></Link>
                <Link className={`eco__box ${d.gerencia.jobs_late ? "is-bad" : ""}`} to="/ordenes-trabajo"><span className="meta">Trabajos de la semana{d.gerencia.jobs_late ? ` · ${d.gerencia.jobs_late} atrasados` : ""}</span><b>{d.gerencia.jobs_week}</b></Link>
                <div className="eco__box"><span className="meta">Cotizaciones enviadas · {d.gerencia.quotes_won_month} ganadas</span><b>{d.gerencia.quotes_sent}</b></div>
                <Link className={`eco__box ${d.gerencia.warranties_soon ? "is-bad" : ""}`} to="/activos?vencen=1"><span className="meta">Garantías por vencer (45 d)</span><b>{d.gerencia.warranties_soon}</b></Link>
              </div>
            </Card>
          )}

          <Card title="Acciones pendientes" extra={<span className="meta">se actualiza en vivo</span>}>
            <div className="pending">
              <div className={d.acciones_pendientes.facturas_vencidas ? "hot" : ""}><b>{d.acciones_pendientes.facturas_vencidas}</b><span>Facturas vencidas</span></div>
              <div><b>{d.acciones_pendientes.facturas_por_cobrar}</b><span>Facturas por cobrar</span></div>
              <div><b>{d.acciones_pendientes.cotizaciones_sin_respuesta}</b><span>Cotizaciones sin respuesta</span></div>
              <div><b>{d.acciones_pendientes.enlaces_abiertos}</b><span>Enlaces de pago abiertos</span></div>
              <div className={d.acciones_pendientes.documentos_rechazados ? "hot" : ""}><b>{d.acciones_pendientes.documentos_rechazados}</b><span>Rechazados por Hacienda</span></div>
              <div className={d.acciones_pendientes.stock_bajo ? "hot" : ""}><b>{d.acciones_pendientes.stock_bajo}</b><span>Productos con stock bajo</span></div>
              {d.acciones_pendientes.cotizaciones_por_aprobar !== undefined && <div className={d.acciones_pendientes.cotizaciones_por_aprobar ? "hot" : ""}><b>{d.acciones_pendientes.cotizaciones_por_aprobar}</b><span>{allows("sales.aprobar") ? <Link to="/cotizaciones?estado=por_aprobar" className="row-link">Descuentos por aprobar</Link> : "Esperando aprobación"}</span></div>}
            </div>
          </Card>

          {d.por_cobrar && d.por_cobrar.length > 0 && (
            <Card title="Mis facturas por cobrar" flush extra={<span className="meta">para dar seguimiento</span>}>
              <table className="table"><thead><tr><th>Factura</th><th>Cliente</th><th>Vence</th><th className="num">Saldo</th><th>Estado</th></tr></thead>
                <tbody>{d.por_cobrar.map((f) => <tr key={f.id}><td><Link className="row-link mono" to={`/facturas/${f.id}`}>{f.number}</Link></td><td>{f.customer || "—"}</td><td className="muted">{fmtDate(f.due_date)}</td><td className="num money">{fmtMoney(f.balance, f.currency)}</td><td><Badge status={f.status} /></td></tr>)}</tbody></table>
            </Card>
          )}

          <div className="grid-2">
            {d.pagos && <Card title="Pagos recientes" flush extra={<Link className="btn btn--ghost btn--sm" to="/pagos">Ver todos</Link>}>
              {d.pagos_recientes.length === 0 ? <Empty hint="Los pagos registrados en facturas aparecen aquí." /> : (
                <table className="table">
                  <thead><tr><th>Referencia</th><th>Método</th><th>Fecha</th><th className="num">Monto</th><th>Estado</th></tr></thead>
                  <tbody>{d.pagos_recientes.map((p) => (
                    <tr key={p.id}><td><Link className="row-link" to={`/facturas/${p.invoice_id}`}>{p.ref || `#${p.id}`}</Link></td><td className="muted" style={{ textTransform: "capitalize" }}>{p.method}</td><td className="muted">{fmtDate(p.date)}</td><td className="num money">{fmtMoney(p.amount, p.currency)}</td><td><Badge status={p.status} /></td></tr>
                  ))}</tbody>
                </table>
              )}
            </Card>}
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
