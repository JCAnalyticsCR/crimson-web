/* Movimientos: todo el libro (comisiones, bonos, pagos acordados y ajustes) con filtros. Totales en colones; si hay
   mas de una moneda, tambien por moneda (falla H: antes sumaba dolares y colones como si fueran lo mismo). */
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty } from "../../ui/components";
import { ESTADO, KIND, errText, type Ledger as LedgerData } from "./types";

export default function Ledger() {
  const { toast, allows } = useSession();
  const [params] = useSearchParams();
  const [f, setF] = useState(() => {
    const e = params.get("estado");
    return { status: e && ESTADO[e] ? e : "", beneficiary: "", kind: "", from: "", to: "", q: "" };
  });
  const [d, setD] = useState<LedgerData | null>(null);
  const [sel, setSel] = useState<Record<number, boolean>>({});
  const puedeAprobar = allows("commissions.aprobar");
  const load = useCallback(() => {
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]);
    api<LedgerData>(`/commissions?${qs}`).then((x) => { setD(x); setSel({}); }).catch((e) => toast(errText(e), "bad"));
  }, [f, toast]);
  useEffect(() => { const t = window.setTimeout(load, 250); return () => window.clearTimeout(t); }, [load]);
  const marcadas = Object.entries(sel).filter(([, v]) => v).map(([k]) => Number(k));
  const aprobar = async () => {
    try { const r = await api<{ actualizadas: number }>("/commissions", { method: "PATCH", json: { ids: marcadas, status: "aprobada" } }); toast(`${r.actualizadas} aprobado(s)`); load(); }
    catch (e) { toast(errText(e), "bad"); }
  };
  const monedas = d ? Object.keys(d.by_currency) : [];

  return (
    <>
      <div className="com-filters">
        <select className="select" aria-label="Estado" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">Todos los estados</option>{Object.entries(ESTADO).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>
        <select className="select" aria-label="Beneficiario" value={f.beneficiary} onChange={(e) => setF({ ...f, beneficiary: e.target.value })}><option value="">Vendedores y aliados</option><option value="vendedor">Vendedores</option><option value="aliado">Aliados</option></select>
        <select className="select" aria-label="Tipo" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="">Todos los tipos</option>{Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <input className="input" type="date" aria-label="Desde" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} />
        <input className="input" type="date" aria-label="Hasta" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} />
        <input className="input" placeholder="Factura, cotización o proyecto" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} />
      </div>
      {d && (
        <div className="com-cards">
          <div className="com-card is-warn"><span className="meta">Por aprobar</span><b className="money">{fmtMoney(d.totals.pendiente)}</b>{d.totals.en_espera ? <small>{d.totals.en_espera} en espera de un dato</small> : null}</div>
          <div className="com-card"><span className="meta">Aprobado sin pagar</span><b className="money">{fmtMoney(d.totals.aprobada)}</b></div>
          <div className="com-card is-good"><span className="meta">Pagado</span><b className="money">{fmtMoney(d.totals.pagada)}</b></div>
          <div className="com-card"><span className="meta">Movimientos</span><b>{d.total_rows}</b>{monedas.length > 1 && <small>{monedas.map((m) => `${m}: ${fmtMoney(d.by_currency[m].pendiente, m)} por aprobar`).join(" · ")}</small>}</div>
        </div>
      )}
      <Card title="Movimientos" flush extra={puedeAprobar && marcadas.length > 0 ? <button className="btn btn--soft btn--sm" onClick={aprobar}>Aprobar {marcadas.length}</button> : <span className="meta">en colones al tipo de cambio de cada movimiento</span>}>
        {!d ? <div style={{ padding: 24, textAlign: "center" }}><span className="spinner" /></div> : d.rows.length === 0 ? <Empty title="Sin movimientos" hint="Se generan solos con el cobro, la aceptación o el cierre del proyecto, según la regla." /> : (
          <div className="com-scroll">
            <table className="table">
              <thead><tr>{puedeAprobar && <th style={{ width: 32 }} />}<th>Fecha</th><th>Beneficiario</th><th>Tipo</th><th>Concepto</th><th>Documento</th><th>Proyecto</th><th className="num">Sobre</th><th className="num">%</th><th className="num">Monto</th><th>Estado</th></tr></thead>
              <tbody>{d.rows.map((r) => (
                <tr key={r.id}>
                  {puedeAprobar && <td>{r.can_approve && r.status === "pendiente" ? <input type="checkbox" aria-label={`Elegir ${r.id}`} checked={!!sel[r.id]} onChange={(e) => setSel({ ...sel, [r.id]: e.target.checked })} /> : null}</td>}
                  <td className="muted">{fmtDate(r.earned_on)}</td>
                  <td style={{ fontWeight: 600 }}>{r.beneficiary_name || "—"}<div className="meta">{r.beneficiary === "aliado" ? "Aliado" : "Vendedor"}</div></td>
                  <td>{KIND[r.kind] || r.kind}</td>
                  <td>{r.description || "—"}{r.hold_reason && <div className="meta">{r.hold_reason}</div>}{r.void_reason && <div className="meta">Anulada: {r.void_reason}</div>}{r.paid_ref && <div className="meta">Pago: {r.paid_ref}</div>}</td>
                  <td className="mono muted">{r.invoice_id ? <Link className="row-link" to={`/facturas/${r.invoice_id}`}>{r.invoice}</Link> : r.quote || "—"}</td>
                  <td className="mono muted">{r.project_id ? <Link className="row-link" to={`/proyectos/${r.project_id}`}>{r.project}</Link> : "—"}</td>
                  <td className="num money">{r.base_amount == null ? <span className="muted" title="Sobre la utilidad (confidencial)">—</span> : Number(r.base_amount) ? fmtMoney(r.base_amount, r.currency) : "—"}</td>
                  <td className="num mono">{r.percent == null ? <span className="muted" title="Sobre la utilidad (confidencial)">—</span> : Number(r.percent) ? `${Number(r.percent)} %` : "—"}</td>
                  <td className={`num money ${Number(r.amount) < 0 ? "com-neg" : ""}`} style={{ fontWeight: 700 }}>{fmtMoney(r.amount, r.currency)}</td>
                  <td><span className={`badge badge--${ESTADO[r.status]?.tone || "muted"}`}>{ESTADO[r.status]?.label || r.status}</span></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
