/* Contable -> Resultados: estado de resultados mensual (GET /accounting/income-statement). «Cuánto vendí y cuánto
   gané» mes a mes, por categoría, en colones y en base devengada. No es contabilidad formal (esa sigue en QuickBooks).
   Escritorio: un mes por columna + total, con la primera columna fija y desplazamiento horizontal si hay muchos meses.
   Tablet 768 px y celular: table--stack, cada concepto pasa a una tarjeta con sus meses. */
import { useEffect, useState } from "react";
import { api, fmtMoney } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Card, Empty, Loading } from "../../../ui/components";
import AuthLink from "../../../ui/AuthLink";

type Row = { key: string; label: string; kind: "section" | "line" | "subtotal" | "result"; values?: string[]; total?: string; auto?: boolean; category_id?: number | null };
type Statement = {
  from: string; to: string; months: string[]; currency: "CRC"; basis: "devengado"; note: string;
  rows: Row[];
  margins: { bruto_pct: (string | null)[]; neto_pct: (string | null)[] };
  inventory_cost: Row | null; sees_costs: boolean;
  counts: { facturas: number; gastos: number };
  warnings: string[];
  summary: { ingresos: string; costo_ventas: string; utilidad_bruta: string; gastos_operacion: string; utilidad_neta: string };
};

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"];
const mesCorto = (mk: string) => { const [y, m] = mk.split("-"); return `${MESES[Number(m) - 1]} ${y}`; };
const ym = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
const shift = (mk: string, n: number) => { const [y, m] = mk.split("-").map(Number); return ym(new Date(y, m - 1 + n, 1)); };

const PRESETS: { key: string; label: string; range: (hoy: string) => [string, string] }[] = [
  { key: "mes", label: "Este mes", range: (h) => [h, h] },
  { key: "3", label: "Últimos 3 meses", range: (h) => [shift(h, -2), h] },
  { key: "12", label: "Últimos 12 meses", range: (h) => [shift(h, -11), h] },
  { key: "anio", label: "Este año", range: (h) => [`${h.slice(0, 4)}-01`, h] },
];

/* ₡ sin decimales en la tabla (se lee mejor de un vistazo); el Excel lleva los céntimos */
const money = (v: string | null | undefined) => (v === null || v === undefined ? "—" : fmtMoney(Math.round(Number(v))));
const neg = (v: string | undefined) => Number(v ?? 0) < 0;

export default function Results() {
  const { toast, allows } = useSession();
  const hoy = ym(new Date());
  const [from, setFrom] = useState(() => shift(hoy, -2));
  const [to, setTo] = useState(hoy);
  const [s, setS] = useState<Statement | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (from > to) { setErr("El mes inicial es posterior al final"); return; }
    setS(null); setErr(null);
    api<Statement>(`/accounting/income-statement?from=${from}&to=${to}`).then(setS).catch((e) => { setErr(e.message); toast(e.message, "bad"); });
  }, [from, to]); // eslint-disable-line react-hooks/exhaustive-deps

  const preset = PRESETS.find((p) => { const [a, b] = p.range(hoy); return a === from && b === to; })?.key;
  const gastos = s ? String(Number(s.summary.costo_ventas) + Number(s.summary.gastos_operacion)) : "0";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="res-bar">
        <div className="acc-seg" role="group" aria-label="Periodo">
          {PRESETS.map((p) => <button key={p.key} type="button" className={preset === p.key ? "is-active" : ""} onClick={() => { const [a, b] = p.range(hoy); setFrom(a); setTo(b); }}>{p.label}</button>)}
        </div>
        <label className="res-month"><span className="meta">Desde</span><input className="input" type="month" value={from} max={to} onChange={(e) => e.target.value && setFrom(e.target.value)} /></label>
        <label className="res-month"><span className="meta">Hasta</span><input className="input" type="month" value={to} min={from} onChange={(e) => e.target.value && setTo(e.target.value)} /></label>
        {allows("accounting.exportar") && s && <AuthLink path={`/accounting/income-statement?from=${s.from}&to=${s.to}&format=xlsx`} download={`estado-resultados-${s.from}-a-${s.to}.xlsx`}>Excel</AuthLink>}
      </div>

      {err ? <Empty title="No se pudo armar el estado de resultados" hint={err} /> : s === null ? <Loading /> : (
        <>
          <div className="res-kpis">
            <div className="kpi__card kpi__card--light"><div className="kpi__label">Ventas sin IVA</div><div className="kpi__value res-kpi">{money(s.summary.ingresos)}</div><div className="kpi__sub">{s.counts.facturas} factura{s.counts.facturas === 1 ? "" : "s"}</div></div>
            <div className="kpi__card kpi__card--light"><div className="kpi__label">Costos y gastos</div><div className="kpi__value res-kpi">{money(gastos)}</div><div className="kpi__sub">{s.counts.gastos} gasto{s.counts.gastos === 1 ? "" : "s"}</div></div>
            <div className="kpi__card"><div className="kpi__label">Ganancia (utilidad neta)</div><div className="kpi__value res-kpi" style={{ color: neg(s.summary.utilidad_neta) ? "#ff8a9a" : undefined }}>{money(s.summary.utilidad_neta)}</div><div className="kpi__sub">antes del impuesto sobre la renta{s.margins.neto_pct[s.months.length] !== null ? ` · ${s.margins.neto_pct[s.months.length]} % de las ventas` : ""}</div></div>
          </div>

          {s.warnings.map((w) => <div key={w} className="acc-warn">⚠ {w}</div>)}

          <Card title={`Estado de resultados · ${mesCorto(s.from)}${s.from !== s.to ? ` a ${mesCorto(s.to)}` : ""}`} extra={<span className="meta">en colones · base devengada</span>} flush>
            <div className="res-scroll">
              <table className="table table--sm table--stack res-table">
                <thead><tr><th>Concepto</th>{s.months.map((m) => <th key={m} className="num">{mesCorto(m)}</th>)}{s.months.length > 1 && <th className="num">Total</th>}</tr></thead>
                <tbody>
                  {s.rows.map((r) => r.kind === "section" ? (
                    <tr key={r.key} className="res-section"><td className="stack-full" colSpan={s.months.length + 2}>{r.label}</td></tr>
                  ) : (
                    <tr key={r.key} className={`res-${r.kind}`}>
                      <td className="stack-full">{r.label}{r.auto && <span className="muted" title="Ubicada por su nombre: elegí su grupo en Categorías"> *</span>}</td>
                      {s.months.map((m, i) => <td key={m} data-label={mesCorto(m)} className={`num money${neg(r.values?.[i]) ? " res-neg" : ""}`}>{money(r.values?.[i])}</td>)}
                      {s.months.length > 1 && <td data-label="Total" className={`num money res-total${neg(r.total) ? " res-neg" : ""}`}>{money(r.total)}</td>}
                    </tr>
                  ))}
                  {([["Margen bruto", s.margins.bruto_pct], ["Margen neto", s.margins.neto_pct]] as [string, (string | null)[]][]).map(([label, v]) => (
                    <tr key={label} className="res-margin">
                      <td className="stack-full">{label} <span className="muted">(% de las ventas)</span></td>
                      {s.months.map((m, i) => <td key={m} data-label={mesCorto(m)} className="num money">{v[i] === null ? "—" : `${v[i]} %`}</td>)}
                      {s.months.length > 1 && <td data-label="Total" className="num money res-total">{v[s.months.length] === null ? "—" : `${v[s.months.length]} %`}</td>}
                    </tr>
                  ))}
                  {s.inventory_cost && (
                    <>
                      <tr className="res-section"><td className="stack-full" colSpan={s.months.length + 2}>Referencia (solo quien ve costos)</td></tr>
                      <tr className="res-line">
                        <td className="stack-full">{s.inventory_cost.label}<div className="muted" style={{ fontSize: 12 }}>No se resta: las compras de equipo ya cuentan en «Costo de ventas».</div></td>
                        {s.months.map((m, i) => <td key={m} data-label={mesCorto(m)} className="num money">{money(s.inventory_cost!.values?.[i])}</td>)}
                        {s.months.length > 1 && <td data-label="Total" className="num money res-total">{money(s.inventory_cost.total)}</td>}
                      </tr>
                    </>
                  )}
                </tbody>
              </table>
            </div>
            <p className="muted res-note">{s.note}</p>
          </Card>
        </>
      )}
    </div>
  );
}
