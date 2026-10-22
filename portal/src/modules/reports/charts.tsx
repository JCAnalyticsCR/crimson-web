/* Graficos de los reportes (reunion 2, pedido 3): SVG y divs propios, sin librerias. Cada grafico lleva su
   aria-label y el monto exacto en el title; las tablas del reporte tienen los numeros completos. */
import { fmtMoney } from "../../lib/api";

export const TONES = ["var(--crimson)", "var(--info)", "var(--ok)", "var(--warn)", "var(--ink-3)", "var(--bad)"];
const n = (v: string | number | null | undefined) => Number(v ?? 0) || 0;

/** Monto corto para ejes y etiquetas: ₡1,2 M · ₡850 k · $12 k. */
export function short(v: number, currency = "CRC") {
  const s = currency === "USD" ? "$" : "₡";
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e6) return `${sign}${s}${(a / 1e6).toLocaleString("es-CR", { maximumFractionDigits: 1 })} M`;
  if (a >= 1e3) return `${sign}${s}${(a / 1e3).toLocaleString("es-CR", { maximumFractionDigits: 0 })} k`;
  return `${sign}${s}${a.toLocaleString("es-CR", { maximumFractionDigits: 0 })}`;
}

/** Columnas verticales (ventas por mes). La ultima, la del periodo, va resaltada. */
export function ColumnChart({ data, currency = "CRC", label }: { data: { label: string; amount: string | number }[]; currency?: string; label: string }) {
  const W = 520, H = 190, top = 18, bottom = 24, gap = 14;
  const max = Math.max(1, ...data.map((d) => n(d.amount)));
  const bw = (W - gap * (data.length + 1)) / Math.max(1, data.length);
  return (
    <svg className="rep-cols" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      <line x1={0} x2={W} y1={H - bottom} y2={H - bottom} />
      {data.map((d, i) => {
        const v = Math.max(0, n(d.amount));
        const h = Math.max(2, (v / max) * (H - top - bottom));
        const x = gap + i * (bw + gap);
        const y = H - bottom - h;
        return (
          <g key={d.label}>
            <title>{`${d.label}: ${fmtMoney(d.amount, currency)}`}</title>
            <rect x={x} y={y} width={bw} height={h} rx={4} className={i === data.length - 1 ? "is-last" : ""} />
            <text className="rep-cols__val" x={x + bw / 2} y={y - 5} textAnchor="middle">{v ? short(v, currency) : ""}</text>
            <text x={x + bw / 2} y={H - 8} textAnchor="middle">{d.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

/** Barras horizontales con su monto (ventas por vendedor, por tipo, gastos por categoria). */
export function BarList({ rows, currency = "CRC", empty = "Sin datos en el periodo.", color }: {
  rows: { label: string; amount: string | number; sub?: string }[]; currency?: string; empty?: string; color?: string;
}) {
  const vis = rows.filter((r) => n(r.amount) !== 0);
  if (!vis.length) return <p className="muted" style={{ margin: 0, fontSize: 13 }}>{empty}</p>;
  const max = Math.max(1, ...vis.map((r) => Math.abs(n(r.amount))));
  return (
    <div className="rep-bars">
      {vis.map((r, i) => (
        <div className="rep-bar" key={`${r.label}-${i}`} title={`${r.label}: ${fmtMoney(r.amount, currency)}`}>
          <span className="rep-bar__label">{r.label}</span>
          <span className="rep-bar__track"><span className="rep-bar__fill" style={{ width: `${(Math.abs(n(r.amount)) / max) * 100}%`, background: n(r.amount) < 0 ? "var(--bad)" : color || TONES[i % TONES.length] }} /></span>
          <span className="rep-bar__value">{fmtMoney(r.amount, currency)}{r.sub && <span className="rep-bar__sub">{r.sub}</span>}</span>
        </div>
      ))}
    </div>
  );
}

/** Una barra apilada con su leyenda (cuentas por cobrar por antiguedad). */
export function StackBar({ parts, currency = "CRC", label }: { parts: { label: string; amount: string | number; color: string; sub?: string }[]; currency?: string; label: string }) {
  const total = parts.reduce((a, p) => a + Math.max(0, n(p.amount)), 0);
  return (
    <div>
      <div className="rep-stack" role="img" aria-label={label}>
        {total > 0 && parts.map((p) => n(p.amount) > 0 && <span key={p.label} title={`${p.label}: ${fmtMoney(p.amount, currency)}`} style={{ width: `${(n(p.amount) / total) * 100}%`, background: p.color }} />)}
      </div>
      <div className="rep-legend">
        {parts.map((p) => <div key={p.label}><i style={{ background: p.color }} /><span>{p.label}{p.sub ? <span className="muted"> · {p.sub}</span> : null}</span><span>{fmtMoney(p.amount, currency)}</span></div>)}
      </div>
    </div>
  );
}

export function Kpi({ label, value, sub, tone, delta }: { label: string; value: string; sub?: string; tone?: "good" | "bad" | "crimson"; delta?: number | null }) {
  return (
    <div className={`rep-kpi ${tone ? `is-${tone}` : ""}`}>
      <span className="meta">{label}</span>
      <b>{value}</b>
      {(sub || (delta !== undefined && delta !== null)) && (
        <small>
          {delta !== undefined && delta !== null && <span className={`rep-delta ${delta >= 0 ? "up" : "down"}`}>{delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toLocaleString("es-CR")} %</span>}
          {delta !== undefined && delta !== null && sub ? " · " : ""}{sub}
        </small>
      )}
    </div>
  );
}
