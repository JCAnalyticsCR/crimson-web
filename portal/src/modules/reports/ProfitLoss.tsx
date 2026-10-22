/* Estado de resultados completo (reunion 2, pedido 1): ingresos por tipo y por vendedor, costo de ventas y gastos por
   categoria (las comisiones pagadas incluidas), IVA cobrado y pagado, utilidad bruta y neta. Todo en una sola moneda
   (colones o dolares) con el tipo de cambio de cada documento. Excel con formato desde el mismo boton. */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import AuthLink from "../../ui/AuthLink";
import { Card, Empty, I, Icon, Loading } from "../../ui/components";
import { BarList, Kpi } from "./charts";

type Amount = string | number;
type Cat = { key: string; label: string; amount: Amount; category_id: number | null; auto: boolean; commissions: boolean; expenses: number };
type Group = { label: string; por_categoria: Cat[]; total: Amount };
type Pnl = {
  from: string; to: string; currency: "CRC" | "USD";
  ingresos: { por_tipo: { key: string; label: string; amount: Amount }[]; por_vendedor: { key: string; label: string; amount: Amount; invoices: number }[]; total: Amount; iva_cobrado: Amount; facturas: number };
  costo_ventas: Group; gastos_operacion: Group;
  utilidad_bruta: Amount; margen_bruto_pct: number | null; utilidad_neta: Amount; margen_neto_pct: number | null;
  comisiones: Amount; gastos_total: Amount;
  iva: { cobrado: Amount; pagado: Amount; acreditable: Amount; no_acreditable: Amount; neto: Amount };
  comisiones_sin_gasto: number; avisos: string[]; can_register_commissions: boolean;
};

export default function ProfitLoss({ from, to }: { from: string; to: string }) {
  const { toast, allows } = useSession();
  const [cur, setCur] = useState<"CRC" | "USD">("CRC");
  const [d, setD] = useState<Pnl | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    setErr(null);
    api<Pnl>(`/reports/resultados/completo?from=${from}&to=${to}&currency=${cur}`).then(setD).catch((e) => { setD(null); setErr(e instanceof Error ? e.message : "No se pudo cargar"); });
  }, [from, to, cur]);
  useEffect(load, [load]);
  const registrar = async () => {
    setBusy(true);
    try {
      const r = await api<{ registrados: number }>("/commissions/paid-expenses", { method: "POST", json: { from, to } });
      toast(`${r.registrados} gasto(s) de comisiones registrados`);
      load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  const m = (v: Amount) => fmtMoney(v, cur);
  const ventas = Number(d?.ingresos.total ?? 0);
  const pct = (v: Amount) => (ventas ? `${((Number(v) / ventas) * 100).toLocaleString("es-CR", { maximumFractionDigits: 1 })} %` : "");
  const head = (t: string) => <tr className="is-head"><td>{t}</td><td className="num">Monto</td><td className="pct">% ventas</td></tr>;
  const line = (label: string, v: Amount, extra?: string) => <tr className="is-line"><td>{label}{extra && <span className="rep-auto">{extra}</span>}</td><td className="num money">{m(v)}</td><td className="pct">{pct(v)}</td></tr>;
  const total = (label: string, v: Amount, result = false) => <tr className={result ? `is-result ${Number(v) < 0 ? "is-neg" : ""}` : "is-total"}><td>{label}</td><td className="num money">{m(v)}</td><td className="pct">{pct(v)}</td></tr>;

  return (
    <>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 14 }}>
        <div className="rep-seg" role="radiogroup" aria-label="Moneda del reporte">
          {(["CRC", "USD"] as const).map((c) => <button key={c} type="button" className={cur === c ? "is-active" : ""} aria-pressed={cur === c} onClick={() => setCur(c)}>{c === "CRC" ? "Colones" : "Dólares"}</button>)}
        </div>
        <span className="muted" style={{ fontSize: 12.5, flex: 1, minWidth: 200 }}>Cada documento con su tipo de cambio; si no lo guardó, el del BCCR de su fecha.</span>
        {allows("reports.exportar") && <AuthLink className="btn btn--crimson btn--sm" path={`/reports/resultados/completo?from=${from}&to=${to}&currency=${cur}&format=xlsx`} download={`estado-resultados-${from}-${to}-${cur}.xlsx`}><Icon d={I.reports} />Descargar Excel</AuthLink>}
      </div>
      {err ? <Card title="Estado de resultados"><Empty title="No se pudo cargar" hint={err} /></Card> : !d ? <Loading /> : (
        <>
          {d.avisos.length > 0 && (
            <div className="rep-warn" role="status">
              <span style={{ flex: 1 }}>{d.avisos.map((a) => <div key={a}>⚠ {a}</div>)}</span>
              {d.can_register_commissions && <button className="btn btn--soft btn--sm" disabled={busy} onClick={registrar}>Registrar comisiones pagadas</button>}
              {allows("accounting.editar") && d.avisos.some((a) => a.includes("Categorías")) && <Link className="btn btn--ghost btn--sm" to="/contabilidad/contable/categorias">Categorías</Link>}
            </div>
          )}
          <div className="rep-kpis">
            <Kpi label="Ingresos (sin IVA)" value={m(d.ingresos.total)} sub={`${d.ingresos.facturas} factura(s)`} tone="crimson" />
            <Kpi label="Utilidad bruta" value={m(d.utilidad_bruta)} sub={d.margen_bruto_pct !== null ? `margen ${d.margen_bruto_pct.toLocaleString("es-CR")} %` : undefined} tone={Number(d.utilidad_bruta) < 0 ? "bad" : "good"} />
            <Kpi label="Gastos (costo + operación)" value={m(d.gastos_total)} sub={`comisiones ${m(d.comisiones)}`} />
            <Kpi label="Utilidad neta" value={m(d.utilidad_neta)} sub={d.margen_neto_pct !== null ? `margen ${d.margen_neto_pct.toLocaleString("es-CR")} %` : undefined} tone={Number(d.utilidad_neta) < 0 ? "bad" : "good"} />
          </div>
          <div className="rep-grid">
            <Card title="Estado de resultados" flush extra={<span className="meta">{cur === "CRC" ? "colones" : "dólares"}</span>}>
              <div style={{ overflowX: "auto" }}>
                <table className="rep-pl">
                  <tbody>
                    {head("Ingresos (sin IVA)")}
                    {d.ingresos.por_tipo.map((t) => <tr key={t.key} className="is-line"><td>{t.label}</td><td className="num money">{m(t.amount)}</td><td className="pct">{pct(t.amount)}</td></tr>)}
                    {total("Total de ingresos", d.ingresos.total)}
                    {head("Costo de ventas")}
                    {d.costo_ventas.por_categoria.length === 0 ? <tr className="is-line"><td className="muted" colSpan={3}>Sin gastos de costo de ventas</td></tr> : d.costo_ventas.por_categoria.map((c) => <tr key={c.key} className="is-line"><td>{c.label}{c.auto && <span className="rep-auto">por su nombre</span>}</td><td className="num money">{m(c.amount)}</td><td className="pct">{pct(c.amount)}</td></tr>)}
                    {total("Total costo de ventas", d.costo_ventas.total)}
                    {total("Utilidad bruta", d.utilidad_bruta, true)}
                    {head("Gastos de operación")}
                    {d.gastos_operacion.por_categoria.length === 0 ? <tr className="is-line"><td className="muted" colSpan={3}>Sin gastos de operación</td></tr> : d.gastos_operacion.por_categoria.map((c) => <tr key={c.key} className="is-line"><td>{c.label}{c.commissions && <span className="rep-auto">comisiones pagadas</span>}{c.auto && !c.commissions && <span className="rep-auto">por su nombre</span>}</td><td className="num money">{m(c.amount)}</td><td className="pct">{pct(c.amount)}</td></tr>)}
                    {total("Total gastos de operación", d.gastos_operacion.total)}
                    {total("Utilidad neta (antes del impuesto sobre la renta)", d.utilidad_neta, true)}
                    {head("IVA del periodo (informativo)")}
                    {line("IVA cobrado en ventas", d.iva.cobrado)}
                    {line("IVA pagado en compras y gastos", d.iva.pagado)}
                    {line("de él, acreditable", d.iva.acreditable)}
                    {line("de él, no acreditable", d.iva.no_acreditable, "ya incluido en los gastos")}
                    {total("IVA neto (cobrado − acreditable)", d.iva.neto)}
                  </tbody>
                </table>
              </div>
              <p className="muted" style={{ fontSize: 12, padding: "10px 18px", margin: 0 }}>El borrador del D-104 (con prorrata y comprobantes por confirmar) está en el reporte IVA.</p>
            </Card>
            <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
              <Card title="Ingresos por tipo"><BarList currency={cur} rows={d.ingresos.por_tipo.map((t) => ({ label: t.label.replace(/ \(.*\)$/, ""), amount: t.amount }))} /></Card>
              <Card title="Ingresos por vendedor"><BarList currency={cur} rows={d.ingresos.por_vendedor.map((v) => ({ label: v.label, amount: v.amount, sub: `${v.invoices} fact.` }))} /></Card>
              <Card title="Gastos por categoría"><BarList currency={cur} color="var(--warn)" rows={[...d.costo_ventas.por_categoria, ...d.gastos_operacion.por_categoria].sort((a, b) => Number(b.amount) - Number(a.amount)).map((c) => ({ label: c.label, amount: c.amount }))} /></Card>
            </div>
          </div>
        </>
      )}
    </>
  );
}
