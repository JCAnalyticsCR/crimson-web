/* IVA del gasto por tarifa (reunión con la contadora, 10/11/2026): tarifa, base, IVA y acreditable.
   Sale de las líneas del XML del proveedor (13 %, 4 %, 2 %, 1 %, exento, exonerado…) o, en un gasto a mano, es una
   sola fila con la tarifa del gasto. Solo lectura: GET /expenses/{id}/tax-breakdown.
   Tablet 768 px: table--stack pasa cada tarifa a una tarjeta con sus etiquetas. */
import { useEffect, useState } from "react";
import { api, fmtMoney } from "../../../lib/api";
import { Loading } from "../../../ui/components";

type Part = {
  kind: "gravado" | "exento" | "exonerado" | "otro_impuesto" | "otros_cargos" | string;
  label: string;
  rate: string | number;
  base: string | number;
  tax: string | number;
  exonerated: string | number;
  acreditable: string | number;
  no_acreditable: string | number;
  prorrata: boolean;
  currency: string;
};
type Breakdown = {
  expense_id: number;
  currency: string;
  source: "xml" | "manual";
  stored: boolean;
  parts: Part[];
  totals: { base: string | number; tax: string | number; acreditable: string | number; no_acreditable: string | number; total: string | number };
};

const n = (v: string | number) => Number(v) || 0;

function credito(p: Part, cur: string) {
  if (p.prorrata) return <span className="badge badge--info">Prorrata</span>;
  if (!n(p.tax)) return <span className="muted">—</span>;
  const acred = n(p.acreditable);
  if (acred === n(p.tax)) return <span className="badge badge--ok">Sí · {fmtMoney(acred, cur)}</span>;
  if (!acred) return <span className="badge badge--muted">No</span>;
  return <span className="badge badge--warn">Parcial · {fmtMoney(acred, cur)}</span>;
}

export default function ExpenseTaxBreakdown({ expenseId, reloadKey }: { expenseId: number; reloadKey?: unknown }) {
  const [data, setData] = useState<Breakdown | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let vivo = true;
    setData(null);
    setError(null);
    api<Breakdown>(`/expenses/${expenseId}/tax-breakdown`)
      .then((d) => vivo && setData(d))
      .catch((e: unknown) => vivo && setError(e instanceof Error ? e.message : "No se pudo cargar el desglose del IVA"));
    return () => {
      vivo = false;
    };
  }, [expenseId, reloadKey]);

  if (error) return <p className="muted" role="alert">{error}</p>;
  if (!data) return <Loading label="Cargando IVA por tarifa…" />;
  const cur = data.currency;

  return (
    <div className="expense-tax">
      <p className="muted" style={{ fontSize: 13, margin: "0 0 8px" }}>
        {data.source === "xml" ? "IVA por tarifa según las líneas del comprobante electrónico." : "Gasto sin comprobante electrónico: una sola tarifa."}
      </p>
      <div style={{ overflowX: "auto" }}>
        <table className="table table--sm table--stack">
          <thead>
            <tr>
              <th>Tarifa</th>
              <th className="num">Base</th>
              <th className="num">IVA</th>
              <th>Acreditable</th>
            </tr>
          </thead>
          <tbody>
            {data.parts.map((p, i) => (
              <tr key={`${p.kind}-${p.rate}-${i}`}>
                <td data-label="Tarifa">
                  <b>{p.label}</b>
                  {n(p.exonerated) > 0 && <div className="muted" style={{ fontSize: 12 }}>Exonerado {fmtMoney(p.exonerated, cur)}</div>}
                </td>
                <td data-label="Base" className="num">{fmtMoney(p.base, cur)}</td>
                <td data-label="IVA" className="num">{fmtMoney(p.tax, cur)}</td>
                <td data-label="Acreditable">{credito(p, cur)}</td>
              </tr>
            ))}
            {data.parts.length > 1 && (
              <tr>
                <td data-label="Total"><b>Total</b></td>
                <td data-label="Base" className="num"><b>{fmtMoney(data.totals.base, cur)}</b></td>
                <td data-label="IVA" className="num"><b>{fmtMoney(data.totals.tax, cur)}</b></td>
                <td data-label="Acreditable"><b>{fmtMoney(data.totals.acreditable, cur)}</b></td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
