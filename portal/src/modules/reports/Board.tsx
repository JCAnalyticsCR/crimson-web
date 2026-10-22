/* Tablero de reportes (reunion 2, pedido 3): ventas del periodo y su variacion, por vendedor, por tipo, ultimos 6
   meses, lo cobrado, lo mas vendido y las cuentas por cobrar por antiguedad. Todo en colones con el tipo de cambio de
   cada documento. El margen solo llega para quien ve costos (el admin): si no viene, no se dibuja. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtMoney } from "../../lib/api";
import { Card, Empty, Loading } from "../../ui/components";
import { BarList, ColumnChart, Kpi, StackBar } from "./charts";

type Amount = string | number;
type Row = { key: string; label: string; amount: Amount; invoices?: number; seller_id?: number | null };
export type BoardData = {
  from: string; to: string; currency: string;
  ventas: { total: Amount; facturas: number; anterior: Amount; variacion_pct: number | null; iva_cobrado: Amount };
  ticket_promedio: Amount; cobrado: Amount;
  por_vendedor: Row[]; por_tipo: Row[];
  serie: { label: string; amount: Amount }[];
  top_productos: { code: string; name: string; quantity: Amount; amount: Amount }[];
  cxc: { total: Amount; facturas: number; vencido: Amount; antiguedad: { key: string; label: string; amount: Amount; count: number }[]; principales: { customer_id: number | null; name: string; amount: Amount }[] };
  margen: { utilidad_bruta: Amount; margen_bruto_pct: number | null; utilidad_neta: Amount; margen_neto_pct: number | null; comisiones: Amount; gastos: Amount } | null;
  can_pnl: boolean;
};

const AGING_COLOR: Record<string, string> = { al_dia: "var(--ok)", d30: "var(--info)", d60: "var(--warn)", d90: "var(--crimson)", mas90: "var(--bad)" };
const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v.toLocaleString("es-CR")} %`);

export default function Board({ from, to, onPnl }: { from: string; to: string; onPnl?: () => void }) {
  const [d, setD] = useState<BoardData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setErr(null);
    api<BoardData>(`/reports/tablero/indicadores?from=${from}&to=${to}`).then(setD).catch((e) => { setD(null); setErr(e instanceof Error ? e.message : "No se pudo cargar el tablero"); });
  }, [from, to]);
  if (err) return <Card title="Tablero"><Empty title="No se pudo cargar" hint={err} /></Card>;
  if (!d) return <Loading />;
  const vencido = Number(d.cxc.vencido) > 0;
  return (
    <>
      <div className="rep-kpis">
        <Kpi label="Ventas del periodo (sin IVA)" value={fmtMoney(d.ventas.total)} delta={d.ventas.variacion_pct} sub={`antes ${fmtMoney(d.ventas.anterior)}`} tone="crimson" />
        <Kpi label="Facturas" value={String(d.ventas.facturas)} sub={`ticket promedio ${fmtMoney(d.ticket_promedio)}`} />
        <Kpi label="Cobrado en el periodo" value={fmtMoney(d.cobrado)} tone="good" />
        <Kpi label="Cuentas por cobrar" value={fmtMoney(d.cxc.total)} sub={`${d.cxc.facturas} factura(s) · vencido ${fmtMoney(d.cxc.vencido)}`} tone={vencido ? "bad" : undefined} />
        {d.margen && <Kpi label="Margen bruto" value={pct(d.margen.margen_bruto_pct)} sub={`utilidad bruta ${fmtMoney(d.margen.utilidad_bruta)}`} tone={Number(d.margen.utilidad_bruta) < 0 ? "bad" : "good"} />}
        {d.margen && <Kpi label="Utilidad neta" value={fmtMoney(d.margen.utilidad_neta)} sub={`margen ${pct(d.margen.margen_neto_pct)} · comisiones ${fmtMoney(d.margen.comisiones)}`} tone={Number(d.margen.utilidad_neta) < 0 ? "bad" : undefined} />}
      </div>
      <div className="rep-grid">
        <Card title="Ventas de los últimos 6 meses" extra={<span className="meta">sin IVA, en colones</span>}>
          <ColumnChart data={d.serie} label="Ventas sin IVA de los últimos seis meses" />
        </Card>
        <Card title="Ventas por vendedor">
          <BarList rows={d.por_vendedor.map((v) => ({ label: v.label, amount: v.amount, sub: v.invoices ? `${v.invoices} fact.` : undefined }))} />
        </Card>
      </div>
      <div className="rep-grid">
        <Card title="Ventas por tipo">
          <BarList rows={d.por_tipo.map((t) => ({ label: t.label, amount: t.amount }))} />
        </Card>
        <Card title="Lo más vendido">
          <BarList rows={d.top_productos.map((p) => ({ label: p.name, amount: p.amount, sub: `${Number(p.quantity).toLocaleString("es-CR")} u.` }))} color="var(--info)" />
        </Card>
      </div>
      <div className="rep-grid">
        <Card title="Cuentas por cobrar por antigüedad" extra={<Link className="btn btn--ghost btn--sm" to="/facturas">Ver facturas</Link>}>
          <StackBar label="Saldo por cobrar según los días de atraso" parts={d.cxc.antiguedad.map((a) => ({ label: a.label, amount: a.amount, sub: a.count ? `${a.count} fact.` : undefined, color: AGING_COLOR[a.key] || "var(--ink-3)" }))} />
        </Card>
        <Card title="Quién debe más">
          <BarList rows={d.cxc.principales.map((c) => ({ label: c.name, amount: c.amount }))} empty="Nadie tiene saldo pendiente." color="var(--warn)" />
        </Card>
      </div>
      {d.can_pnl && onPnl && <div style={{ display: "flex", justifyContent: "flex-end" }}><button className="btn btn--soft btn--sm" onClick={onPnl}>Ver el estado de resultados del periodo</button></div>}
    </>
  );
}
