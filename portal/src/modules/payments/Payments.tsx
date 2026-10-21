/* Pagos: listado de transacciones + cierre diario del periodo (plan 3.7/5.3). */
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, fmtDate, fmtMoney, type Payment } from "../../lib/api";
import { Badge, Card, Empty, I, Icon, Loading } from "../../ui/components";
import AuthLink from "../../ui/AuthLink";
import { useSession } from "../../app/session";
import { TrashLocked } from "../../ui/TrashButton";
import { LOCKED } from "../../ui/trash";
import { hoyCR, inicioMesCR } from "../../lib/fechaCR";

type Cierre = { columns: string[]; rows: (string | number)[][]; totals: { total: string } | null; from: string; to: string };

export default function Payments() {
  const { allows } = useSession();
  const nav = useNavigate();
  const [items, setItems] = useState<Payment[] | null>(null);
  const [cierre, setCierre] = useState<Cierre | null>(null);
  const [range, setRange] = useState({ from: inicioMesCR(), to: hoyCR() });
  // el cierre es de toda la empresa (reports.ver + payments.ver + sales.ver_todo): el vendedor ve solo sus cobros y
  // pedirlo le daba 403 sin capturar y la tarjeta quedaba "Cargando…" para siempre
  const verCierre = allows("reports.ver") && allows("payments.ver") && allows("sales.ver_todo");
  const vacio: Cierre = { columns: [], rows: [], totals: null, from: range.from, to: range.to };
  const load = () => {
    api<Payment[]>("/payments?limit=100").then(setItems).catch(() => setItems([]));
    if (verCierre) api<Cierre>(`/reports/cierre?from=${range.from}&to=${range.to}`).then(setCierre).catch(() => setCierre(vacio));
  };
  useEffect(load, [range.from, range.to, verCierre]);
  const configurar = allows("settings.configurar");
  const metodos = (
    <Card title="Métodos de pago">
      <p className="muted" style={{ fontSize: 13 }}>Manuales (efectivo, SINPE Móvil, transferencia) se confirman desde la factura con su referencia. Los pagos en línea (ONVO / PayPal) entran solos por webhook firmado y aparecen con proveedor <b>onvo</b>.</p>
      {(configurar || allows("reports.ver")) && (
        <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
          {configurar && <Link className="btn btn--soft btn--sm" to="/ajustes?tab=pagos">Configurar pasarelas y métodos</Link>}
          {allows("reports.ver") && <Link className="btn btn--ghost btn--sm" to="/reportes">Ir a reportes</Link>}
        </div>
      )}
    </Card>
  );

  return (
    <>
      <div className="page-head">
        <div><div className="meta">04 · Cobros</div><h1 className="h1">Pagos</h1></div>
        <div className="page-head__actions">
          {verCierre && <input className="input" type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} style={{ width: 150 }} />}
          {verCierre && <input className="input" type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} style={{ width: 150 }} />}
          {allows("sales.ver_todo") && allows("reports.exportar") && <AuthLink path={`/reports/cierre?from=${range.from}&to=${range.to}&format=xlsx`} download={`cierre-${range.from}-${range.to}.xlsx`}><Icon d={I.reports} />Cierre en Excel</AuthLink>}
          <button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} /></button>
        </div>
      </div>
      {verCierre ? (
        <div className="grid-2">
          <Card title="Cierre del periodo por método" flush extra={cierre?.totals && <span className="money" style={{ fontWeight: 700 }}>{fmtMoney(cierre.totals.total)}</span>}>
            {!cierre ? <Loading /> : cierre.rows.length === 0 ? <Empty hint="Los cobros confirmados del periodo se agrupan aquí por día y método." /> : (
              <table className="table"><thead><tr><th>Fecha</th><th>Método</th><th>Divisa</th><th className="num">Monto</th><th className="num">Pagos</th></tr></thead>
                <tbody>{cierre.rows.map((r, i) => <tr key={i}><td className="muted">{fmtDate(String(r[0]))}</td><td style={{ textTransform: "capitalize" }}>{r[1]}</td><td>{r[2]}</td><td className="num money">{fmtMoney(r[3], String(r[2]))}</td><td className="num mono">{r[4]}</td></tr>)}</tbody></table>
            )}
          </Card>
          {metodos}
        </div>
      ) : metodos}
      <Card title="Transacciones recientes" flush>
        {items === null ? <Loading /> : items.length === 0 ? <Empty hint="Registrá un pago desde una factura o compartí un enlace de pago." /> : (
          <table className="table"><thead><tr><th>Fecha</th><th>Factura</th><th>Cliente</th><th>Método</th><th>Tipo</th><th>Referencia</th><th>Proveedor</th><th className="num">Monto</th><th>Estado</th><th /></tr></thead>
            <tbody>{items.map((p) => {
              // toda la fila abre la factura en la misma pestaña
              const go = p.invoice_id ? () => nav(`/facturas/${p.invoice_id}`) : undefined;
              return (
                <tr key={p.id} onClick={go} style={go ? { cursor: "pointer" } : undefined}>
                  <td className="muted">{fmtDate(p.paid_at)}</td>
                  <td>{p.invoice_id ? <Link className="row-link mono" to={`/facturas/${p.invoice_id}`} onClick={(e) => e.stopPropagation()}>{p.invoice_number || `#${p.invoice_id}`}</Link> : "—"}</td>
                  <td>{p.customer || "—"}</td>
                  <td style={{ textTransform: "capitalize" }}>{p.method}</td><td className="muted" style={{ textTransform: "capitalize" }}>{p.kind}</td>
                  <td className="mono muted">{p.external_ref || "—"}</td><td className="muted">{p.provider}</td>
                  <td className="num money">{fmtMoney(p.amount, p.currency)}</td><td><Badge status={p.status} /></td>
                  <td className="num row-actions"><TrashLocked reason={LOCKED.payment} /></td>
                </tr>
              );
            })}</tbody></table>
        )}
      </Card>
    </>
  );
}
