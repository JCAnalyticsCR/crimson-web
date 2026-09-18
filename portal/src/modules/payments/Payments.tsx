/* Pagos: listado de transacciones + cierre diario del periodo (plan 3.7/5.3). */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney, type Payment } from "../../lib/api";
import { Badge, Card, Empty, I, Icon } from "../../ui/components";

type Cierre = { columns: string[]; rows: (string | number)[][]; totals: { total: string } | null; from: string; to: string };

export default function Payments() {
  const [items, setItems] = useState<(Payment & { invoice_id?: number })[]>([]);
  const [cierre, setCierre] = useState<Cierre | null>(null);
  const [range, setRange] = useState({ from: new Date(new Date().setDate(1)).toISOString().slice(0, 10), to: new Date().toISOString().slice(0, 10) });
  const load = () => { api<Payment[]>("/payments?limit=100").then(setItems); api<Cierre>(`/reports/cierre?from=${range.from}&to=${range.to}`).then(setCierre); };
  useEffect(load, [range.from, range.to]);

  return (
    <>
      <div className="page-head">
        <div><div className="meta">04 · Cobros</div><h1 className="h1">Pagos</h1></div>
        <div className="page-head__actions">
          <input className="input" type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} style={{ width: 150 }} />
          <input className="input" type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} style={{ width: 150 }} />
          <a className="btn btn--ghost btn--sm" href={`/api/reports/cierre?from=${range.from}&to=${range.to}&format=xlsx`} target="_blank" rel="noopener"><Icon d={I.reports} />Cierre en Excel</a>
          <button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} /></button>
        </div>
      </div>
      <div className="grid-2">
        <Card title="Cierre del periodo por método" flush extra={cierre?.totals && <span className="money" style={{ fontWeight: 700 }}>{fmtMoney(cierre.totals.total)}</span>}>
          {!cierre || cierre.rows.length === 0 ? <Empty hint="Los cobros confirmados del periodo se agrupan aquí por día y método." /> : (
            <table className="table"><thead><tr><th>Fecha</th><th>Método</th><th>Divisa</th><th className="num">Monto</th><th className="num">Pagos</th></tr></thead>
              <tbody>{cierre.rows.map((r, i) => <tr key={i}><td className="muted">{fmtDate(String(r[0]))}</td><td style={{ textTransform: "capitalize" }}>{r[1]}</td><td>{r[2]}</td><td className="num money">{fmtMoney(r[3], String(r[2]))}</td><td className="num mono">{r[4]}</td></tr>)}</tbody></table>
          )}
        </Card>
        <Card title="Métodos de pago">
          <p className="muted" style={{ fontSize: 13 }}>Manuales (efectivo, SINPE Móvil, transferencia) se confirman desde la factura con su referencia. Los pagos en línea (ONVO / PayPal) entran solos por webhook firmado y aparecen con proveedor <b>onvo</b>.</p>
          <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
            <Link className="btn btn--soft btn--sm" to="/ajustes?tab=pagos">Configurar pasarelas y métodos</Link>
            <Link className="btn btn--ghost btn--sm" to="/reportes">Ir a reportes</Link>
          </div>
        </Card>
      </div>
      <Card title="Transacciones recientes" flush>
        {items.length === 0 ? <Empty hint="Registrá un pago desde una factura o compartí un enlace de pago." /> : (
          <table className="table"><thead><tr><th>Fecha</th><th>Factura</th><th>Método</th><th>Tipo</th><th>Referencia</th><th>Proveedor</th><th className="num">Monto</th><th>Estado</th></tr></thead>
            <tbody>{items.map((p) => <tr key={p.id}><td className="muted">{fmtDate(p.paid_at)}</td><td>{p.invoice_id ? <Link className="row-link mono" to={`/facturas/${p.invoice_id}`}>#{p.invoice_id}</Link> : "—"}</td><td style={{ textTransform: "capitalize" }}>{p.method}</td><td className="muted" style={{ textTransform: "capitalize" }}>{p.kind}</td><td className="mono muted">{p.external_ref || "—"}</td><td className="muted">{p.provider}</td><td className="num money">{fmtMoney(p.amount, p.currency)}</td><td><Badge status={p.status} /></td></tr>)}</tbody></table>
        )}
      </Card>
    </>
  );
}
