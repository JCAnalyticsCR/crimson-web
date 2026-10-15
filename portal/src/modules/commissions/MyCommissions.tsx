/* Mis comisiones (13/10, 34:35): "va a poder ver cuanto esta ganando en tiempo real". Lo ganado del mes, lo que
   ganaria si le pagan sus facturas, lo del embudo, su regla y sus movimientos. Se actualiza al volver a la pestaña
   y cada minuto mientras esta visible. Montos sin IVA, en colones. */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney, parseTs } from "../../lib/api";
import { Card, Empty } from "../../ui/components";
import MonthBars from "./MonthBars";
import { ESTADO, errText, type Ledger, type Summary } from "./types";

export default function MyCommissions() {
  const [s, setS] = useState<Summary | null>(null);
  const [rows, setRows] = useState<Ledger | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    api<Summary>("/commissions/summary").then((x) => { setS(x); setErr(null); }).catch((e) => setErr(errText(e)));
    api<Ledger>("/commissions?limit=100").then(setRows).catch(() => setRows(null));
  }, []);
  useEffect(() => {
    load();
    const t = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, 60000);
    const v = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", v);
    return () => { window.clearInterval(t); document.removeEventListener("visibilitychange", v); };
  }, [load]);

  if (err) return <p style={{ color: "var(--bad)" }}>{err}</p>;
  if (!s) return <div style={{ padding: 40, textAlign: "center" }}><span className="spinner" /></div>;
  const extra = Number(s.si_aceptan.total) > 0 || Number(s.en_curso.total) > 0;
  return (
    <>
      <div className="com-cards">
        <div className="com-card is-good"><span className="meta">Ganado este mes</span><b className="money">{fmtMoney(s.ganado.total)}</b><small>{s.ganado.en_espera ? `${s.ganado.en_espera} en cálculo · ` : ""}pendiente {fmtMoney(s.ganado.pendiente)}</small></div>
        <div className="com-card"><span className="meta">Pagado este mes</span><b className="money">{fmtMoney(s.pagado)}</b><small>lo que ya te depositaron</small></div>
        <div className="com-card is-warn"><span className="meta">Por cobrar</span><b className="money">{fmtMoney(s.por_cobrar.total)}</b><small>si te pagan tus {s.por_cobrar.facturas} factura{s.por_cobrar.facturas !== 1 ? "s" : ""} abierta{s.por_cobrar.facturas !== 1 ? "s" : ""}{s.por_cobrar.estimado ? " · estimado" : ""}</small></div>
        <div className="com-card"><span className="meta">En el embudo</span><b className="money">{fmtMoney(s.embudo.total)}</b><small>estimado según la probabilidad de {s.embudo.oportunidades} oportunidad{s.embudo.oportunidades !== 1 ? "es" : ""}</small></div>
      </div>
      {extra && (
        <p className="muted" style={{ fontSize: 13, marginTop: -6 }}>
          {Number(s.si_aceptan.total) > 0 && <>Si aceptan tus {s.si_aceptan.cotizaciones} cotización(es) enviadas: <b className="money">{fmtMoney(s.si_aceptan.total)}</b>. </>}
          {Number(s.en_curso.total) > 0 && <>Al completar {s.en_curso.proyectos} proyecto(s) en curso: <b className="money">{fmtMoney(s.en_curso.total)}</b>{s.en_curso.estimado ? " (estimado)" : ""}.</>}
        </p>
      )}
      <div className="grid-2">
        <Card title="Últimos 12 meses" extra={<span className="meta">Actualizado {parseTs(s.actualizado).toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit" })}</span>}>
          <MonthBars rows={s.historial} a="ganado" b="pagado" labelA="Ganado" labelB="Pagado" />
        </Card>
        <Card title="Tu regla">
          {s.reglas.length === 0 ? <p className="muted" style={{ margin: 0 }}>Todavía no tenés una regla de comisión. Pedísela a administración.</p> : (
            <ul style={{ margin: 0, paddingLeft: 18 }}>{s.reglas.map((r) => <li key={r.id} style={{ marginBottom: 4 }}>{r.summary}{r.general ? <span className="muted"> · regla de todos los vendedores</span> : null}</li>)}</ul>
          )}
          <p className="com-note">Montos sin IVA. La comisión nace con el cobro (o con el evento de tu regla) y pasa por aprobación.</p>
        </Card>
      </div>
      <Card title="Mis movimientos" flush>
        {!rows ? <div style={{ padding: 24, textAlign: "center" }}><span className="spinner" /></div> : rows.rows.length === 0 ? <Empty title="Todavía no hay comisiones" hint="Se generan solas cuando entra el pago de tus facturas." /> : (
          <div className="com-scroll">
            <table className="table">
              <thead><tr><th>Fecha</th><th>Concepto</th><th>Documento</th><th className="num">Monto</th><th>Estado</th></tr></thead>
              <tbody>{rows.rows.map((r) => (
                <tr key={r.id}>
                  <td className="muted">{fmtDate(r.earned_on)}</td>
                  <td>{r.description || "Comisión"}{r.base === "margen" && r.base_amount == null ? <div className="meta">Sobre la utilidad (confidencial)</div> : null}{r.hold_reason ? <div className="meta">{r.hold_reason}</div> : null}</td>
                  <td className="mono muted">{r.invoice_id ? <Link className="row-link" to={`/facturas/${r.invoice_id}`}>{r.invoice}</Link> : r.quote || r.project || "—"}</td>
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
