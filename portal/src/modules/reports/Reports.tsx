/* Reportes: tablero visual, estado de resultados completo (reunion 2, pedidos 1 y 3) y la cuadricula de reportes ->
   vista en pantalla + descarga Excel (regla del plan 3.6). ?vista=tablero|resultados|todos elige la pestana. */
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, I, Icon, Loading } from "../../ui/components";
import AuthLink from "../../ui/AuthLink";
import Board from "./Board";
import ProfitLoss from "./ProfitLoss";
import "./reports.css";

type Cat = { key: string; title: string; description: string };
type Rep = { key: string; title: string; from: string; to: string; columns: string[]; rows: (string | number | null)[][]; totals: Record<string, string | number> | null };
const MONEY_COLS = /subtotal|descuento|impuesto|total|saldo|monto|iva|base|venta|precio/i;
// totales que son conteos, no dinero (reporte de ordenes)
const COUNT_TOTALS = new Set(["Órdenes", "Pagadas", "Líneas", "Registros", "Pendientes", "Conciliados"]);
type Vista = "tablero" | "resultados" | "todos";

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export default function Reports() {
  // ?r=<clave>&from=&to= (enlaces "en pantalla" del panel Tributario): reporte y rango iniciales
  const [params, setParams] = useSearchParams();
  const { allows } = useSession();
  const [cat, setCat] = useState<Cat[] | null>(null);
  // el reporte inicial sale del catalogo del rol (?r= solo si esta en el): "facturacion" fijo le daba 403 al supervisor,
  // a bodega y a caja, con el titulo en "…" y "Descargar Excel" apuntando a un reporte prohibido
  const [key, setKey] = useState<string | null>(null);
  const [rep, setRep] = useState<Rep | null>(null);
  const [range, setRange] = useState(() => {
    const from = params.get("from") || "", to = params.get("to") || "";
    return {
      from: ISO_DAY.test(from) ? from : new Date(new Date().setDate(1)).toISOString().slice(0, 10),
      to: ISO_DAY.test(to) ? to : new Date().toISOString().slice(0, 10),
    };
  });
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    api<Cat[]>("/reports").then((c) => {
      setCat(c);
      const pedido = params.get("r");
      setKey(pedido && c.some((x) => x.key === pedido) ? pedido : c[0]?.key ?? null);
    }).catch(() => setCat([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!key) return;
    setLoading(true);
    api<Rep>(`/reports/${key}?from=${range.from}&to=${range.to}`).then(setRep).catch(() => setRep(null)).finally(() => setLoading(false));
  }, [key, range.from, range.to]);
  const xlsx = `/reports/${key}?from=${range.from}&to=${range.to}&format=xlsx`;
  // pestanas: el tablero es de quien ve las ventas de toda la empresa; el estado de resultados, de quien tiene el reporte
  // el tablero pide ver todas las ventas y los reportes de ventas (caja y soporte no: routers/report_board.py)
  const conTablero = allows("sales.ver_todo") && allows("sales.exportar");
  const conResultados = !!cat?.some((c) => c.key === "resultados");
  const vistas: [Vista, string][] = [...(conTablero ? [["tablero", "Tablero"]] as [Vista, string][] : []), ...(conResultados ? [["resultados", "Estado de resultados"]] as [Vista, string][] : []), ["todos", "Todos los reportes"]];
  const pedida = params.get("vista") as Vista | null;
  const vista: Vista = params.get("r") && !pedida ? "todos" : vistas.find(([k]) => k === pedida)?.[0] ?? (cat === null && pedida ? pedida : vistas[0][0]);
  const ir = (v: Vista) => { const n = new URLSearchParams(params); n.set("vista", v); n.delete("r"); setParams(n, { replace: true }); };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">08 · Reportes</div><h1 className="h1">Reportes</h1></div>
        <div className="page-head__actions">
          <input className="input" type="date" aria-label="Desde" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} style={{ width: 150 }} />
          <input className="input" type="date" aria-label="Hasta" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} style={{ width: 150 }} />
          {vista === "todos" && key && <AuthLink className="btn btn--crimson" path={xlsx} download={`${key}-${range.from}-${range.to}.xlsx`}><Icon d={I.reports} />Descargar Excel</AuthLink>}
        </div>
      </div>
      {vistas.length > 1 && <div className="tabs rep-tabs" role="tablist">{vistas.map(([k, l]) => <button key={k} role="tab" aria-selected={vista === k} className={vista === k ? "is-active" : ""} onClick={() => ir(k)}>{l}</button>)}</div>}
      {vista === "tablero" && conTablero && <Board from={range.from} to={range.to} onPnl={conResultados ? () => ir("resultados") : undefined} />}
      {vista === "resultados" && conResultados && <ProfitLoss from={range.from} to={range.to} />}
      {vista === "todos" && <>
      <div className="actions-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
        {(cat || []).map((c) => (
          <button key={c.key} className="action" onClick={() => setKey(c.key)} style={{ textAlign: "left", cursor: "pointer", borderColor: key === c.key ? "var(--crimson)" : undefined, background: key === c.key ? "var(--crimson-soft)" : undefined }}>
            <i><Icon d={I.reports} /></i><div><b>{c.title}</b><span>{c.description}</span></div>
          </button>
        ))}
      </div>
      {cat !== null && cat.length === 0 ? <Card title="Reportes"><Empty hint="Tu rol no tiene reportes disponibles." /></Card> : <Card title={rep?.title || "…"} flush extra={<span className="meta">{rep ? `${rep.from} → ${rep.to} · ${rep.rows.length} filas` : ""}{loading && <span className="spinner" style={{ display: "inline-block", marginLeft: 10, verticalAlign: "middle" }} />}</span>}>
        {!rep && loading ? <Loading /> : !rep || rep.rows.length === 0 ? <Empty hint="No hay datos en el periodo seleccionado." /> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead><tr>{rep.columns.map((c) => <th key={c} className={MONEY_COLS.test(c) ? "num" : ""}>{c}</th>)}</tr></thead>
              <tbody>{rep.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className={MONEY_COLS.test(rep.columns[j]) && typeof v !== "string" ? "num money" : typeof v === "number" ? "num mono" : ""}>{v === null ? "—" : MONEY_COLS.test(rep.columns[j]) && (typeof v === "number" || /^-?\d+(\.\d+)?$/.test(String(v))) ? fmtMoney(v) : String(v)}</td>)}</tr>)}</tbody>
            </table>
            {rep.totals && <div style={{ display: "flex", gap: 22, padding: "12px 18px", borderTop: "1px solid var(--hair)", justifyContent: "flex-end" }}>{Object.entries(rep.totals).map(([k, v]) => <span key={k} className="muted" style={{ fontSize: 13 }}>{k}: <b className="money" style={{ color: "var(--text)" }}>{k === "n" || COUNT_TOTALS.has(k) || k.startsWith("Provisionales") ? v : k.includes("%") ? `${Number(v).toFixed(1)}%` : fmtMoney(v)}</b></span>)}</div>}
          </div>
        )}
      </Card>}
      </>}
    </>
  );
}
