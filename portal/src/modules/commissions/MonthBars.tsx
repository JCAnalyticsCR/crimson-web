/* Barras por mes, dos series (p. ej. ganado contra pagado). Sin librerias: divs con alto proporcional y el detalle
   en el title de cada columna (la tabla de al lado tiene los numeros exactos). */
import { fmtMoney } from "../../lib/api";
import { mesLbl } from "./types";

export default function MonthBars({ rows, a, b, labelA, labelB, currency = "CRC" }: {
  rows: { mes: string; [k: string]: string | number | null | undefined }[]; a: string; b: string; labelA: string; labelB: string; currency?: string;
}) {
  const val = (r: (typeof rows)[number], k: string) => Math.max(0, Number(r[k]) || 0);
  const max = Math.max(1, ...rows.map((r) => Math.max(val(r, a), val(r, b))));
  return (
    <div>
      <div className="com-bars" role="img" aria-label={`${labelA} contra ${labelB} por mes; los montos exactos están en el detalle`}>
        {rows.map((r) => (
          <div key={r.mes} className="com-bars__col" title={`${mesLbl(r.mes)} · ${labelA}: ${fmtMoney(r[a] as string, currency)} · ${labelB}: ${fmtMoney(r[b] as string, currency)}`}>
            <div className="com-bars__pair">
              <div className="com-bars__a" style={{ height: `${Math.max(2, (val(r, a) / max) * 120)}px` }} />
              <div className="com-bars__b" style={{ height: `${Math.max(2, (val(r, b) / max) * 120)}px` }} />
            </div>
            <span className="com-bars__lbl">{mesLbl(r.mes)}</span>
          </div>
        ))}
      </div>
      <div className="com-legend"><span><i style={{ background: "var(--crimson)" }} />{labelA}</span><span><i style={{ background: "var(--ok)" }} />{labelB}</span></div>
    </div>
  );
}
