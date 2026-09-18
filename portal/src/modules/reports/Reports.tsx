/* Reportes: cuadricula de reportes -> vista en pantalla + descarga Excel (regla del plan 3.6). */
import { useEffect, useState } from "react";
import { api, fmtMoney } from "../../lib/api";
import { Card, Empty, I, Icon } from "../../ui/components";
import AuthLink from "../../ui/AuthLink";

type Cat = { key: string; title: string; description: string };
type Rep = { key: string; title: string; from: string; to: string; columns: string[]; rows: (string | number | null)[][]; totals: Record<string, string | number> | null };
const MONEY_COLS = /subtotal|descuento|impuesto|total|saldo|monto|iva|base|venta|precio/i;

export default function Reports() {
  const [cat, setCat] = useState<Cat[]>([]);
  const [key, setKey] = useState("facturacion");
  const [rep, setRep] = useState<Rep | null>(null);
  const [range, setRange] = useState({ from: new Date(new Date().setDate(1)).toISOString().slice(0, 10), to: new Date().toISOString().slice(0, 10) });
  const [loading, setLoading] = useState(false);
  useEffect(() => { api<Cat[]>("/reports").then(setCat); }, []);
  useEffect(() => { setLoading(true); api<Rep>(`/reports/${key}?from=${range.from}&to=${range.to}`).then(setRep).finally(() => setLoading(false)); }, [key, range.from, range.to]);
  const xlsx = `/reports/${key}?from=${range.from}&to=${range.to}&format=xlsx`;

  return (
    <>
      <div className="page-head">
        <div><div className="meta">08 · Reportes</div><h1 className="h1">Reportes</h1></div>
        <div className="page-head__actions">
          <input className="input" type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} style={{ width: 150 }} />
          <input className="input" type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} style={{ width: 150 }} />
          <AuthLink className="btn btn--crimson" path={xlsx} download={`${key}-${range.from}-${range.to}.xlsx`}><Icon d={I.reports} />Descargar Excel</AuthLink>
        </div>
      </div>
      <div className="actions-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
        {cat.map((c) => (
          <button key={c.key} className="action" onClick={() => setKey(c.key)} style={{ textAlign: "left", cursor: "pointer", borderColor: key === c.key ? "var(--crimson)" : undefined, background: key === c.key ? "var(--crimson-soft)" : undefined }}>
            <i><Icon d={I.reports} /></i><div><b>{c.title}</b><span>{c.description}</span></div>
          </button>
        ))}
      </div>
      <Card title={rep?.title || "…"} flush extra={<span className="meta">{rep ? `${rep.from} → ${rep.to} · ${rep.rows.length} filas` : ""}{loading && <span className="spinner" style={{ display: "inline-block", marginLeft: 10, verticalAlign: "middle" }} />}</span>}>
        {!rep || rep.rows.length === 0 ? <Empty hint="No hay datos en el periodo seleccionado." /> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead><tr>{rep.columns.map((c) => <th key={c} className={MONEY_COLS.test(c) ? "num" : ""}>{c}</th>)}</tr></thead>
              <tbody>{rep.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className={MONEY_COLS.test(rep.columns[j]) && typeof v !== "string" ? "num money" : typeof v === "number" ? "num mono" : ""}>{v === null ? "—" : MONEY_COLS.test(rep.columns[j]) && (typeof v === "number" || /^-?\d+(\.\d+)?$/.test(String(v))) ? fmtMoney(v) : String(v)}</td>)}</tr>)}</tbody>
            </table>
            {rep.totals && <div style={{ display: "flex", gap: 22, padding: "12px 18px", borderTop: "1px solid var(--hair)", justifyContent: "flex-end" }}>{Object.entries(rep.totals).map(([k, v]) => <span key={k} className="muted" style={{ fontSize: 13 }}>{k}: <b className="money" style={{ color: "var(--text)" }}>{k === "n" ? v : fmtMoney(v)}</b></span>)}</div>}
          </div>
        )}
      </Card>
    </>
  );
}
