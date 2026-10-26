/* Mis comisiones (13/10, 34:35): "va a poder ver cuanto esta ganando en tiempo real". Lo ganado del mes, lo que
   ganaria si le pagan sus facturas, lo del embudo, su regla y sus movimientos. Se actualiza al volver a la pestaña
   y cada minuto mientras esta visible. Montos sin IVA, en colones.
   26/10: se elige el mes (en la lista o tocando su barra en el grafico): lo ganado, lo pagado y los movimientos son de
   ese mes; el grafico muestra 6 o 12 meses. Por cobrar y el embudo son siempre de hoy. En el celular la lista de
   movimientos pasa a tarjetas. */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney, parseTs } from "../../lib/api";
import { Card, Empty } from "../../ui/components";
import MonthBars from "./MonthBars";
import { ESTADO, errText, type Ledger, type Summary } from "./types";

const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
const hoyMes = () => {
  const d = new Date(new Date().toLocaleString("en-US", { timeZone: "America/Costa_Rica" }));
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
};
const mesLargo = (k: string) => { const [y, m] = k.split("-"); return `${MESES[Number(m) - 1] || m} ${y}`; };
const rango = (k: string) => {
  const [y, m] = k.split("-").map(Number);
  const last = new Date(y, m, 0).getDate();
  return { from: `${k}-01`, to: `${k}-${String(last).padStart(2, "0")}` };
};

export default function MyCommissions() {
  const [s, setS] = useState<Summary | null>(null);
  const [rows, setRows] = useState<Ledger | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [mes, setMes] = useState(hoyMes);
  const [meses, setMeses] = useState<6 | 12>(() => (typeof window !== "undefined" && window.innerWidth < 640 ? 6 : 12));
  const actual = mes === hoyMes();
  const load = useCallback(() => {
    const { from, to } = rango(mes);
    api<Summary>(`/commissions/summary?from=${from}&to=${to}`).then((x) => { setS(x); setErr(null); }).catch((e) => setErr(errText(e)));
    api<Ledger>(`/commissions?from=${from}&to=${to}&limit=100`).then(setRows).catch(() => setRows(null));
  }, [mes]);
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
  const enMes = actual ? "este mes" : `en ${mesLargo(mes)}`;
  const opciones = [...s.historial.map((h) => h.mes)].reverse();
  if (!opciones.includes(mes)) opciones.unshift(mes);
  return (
    <>
      <div className="com-filter">
        <select className="select" value={mes} onChange={(e) => setMes(e.target.value)} aria-label="Mes">
          {opciones.map((k) => <option key={k} value={k}>{k === hoyMes() ? `Este mes · ${mesLargo(k)}` : mesLargo(k)}</option>)}
        </select>
        {!actual && <button className="btn btn--ghost btn--sm" onClick={() => setMes(hoyMes())}>Volver a este mes</button>}
      </div>
      <div className="com-cards">
        <div className="com-card is-good"><span className="meta">Ganado {enMes}</span><b className="money">{fmtMoney(s.ganado.total)}</b><small>{s.ganado.en_espera ? `${s.ganado.en_espera} en cálculo · ` : ""}{Number(s.ganado.pendiente) > 0 ? <>de eso, <b>{fmtMoney(s.ganado.pendiente)} pendiente de aprobación</b></> : "todo aprobado"}</small></div>
        <div className="com-card"><span className="meta">Pagado {enMes}</span><b className="money">{fmtMoney(s.pagado)}</b><small>lo que ya te depositaron</small></div>
        <div className="com-card is-warn"><span className="meta">Por cobrar · hoy</span><b className="money">{fmtMoney(s.por_cobrar.total)}</b><small>si te pagan tus {s.por_cobrar.facturas} factura{s.por_cobrar.facturas !== 1 ? "s" : ""} abierta{s.por_cobrar.facturas !== 1 ? "s" : ""}{s.por_cobrar.estimado ? " · estimado" : ""}</small></div>
        <div className="com-card"><span className="meta">En el embudo · hoy</span><b className="money">{fmtMoney(s.embudo.total)}</b><small>estimado según la probabilidad de {s.embudo.oportunidades} oportunidad{s.embudo.oportunidades !== 1 ? "es" : ""}</small></div>
      </div>
      {extra && (
        <p className="muted" style={{ fontSize: 13, marginTop: -6 }}>
          {Number(s.si_aceptan.total) > 0 && <>Si aceptan tus {s.si_aceptan.cotizaciones} cotización(es) enviadas: <b className="money">{fmtMoney(s.si_aceptan.total)}</b>. </>}
          {Number(s.en_curso.total) > 0 && <>Al completar {s.en_curso.proyectos} proyecto(s) en curso: <b className="money">{fmtMoney(s.en_curso.total)}</b>{s.en_curso.estimado ? " (estimado)" : ""}.</>}
        </p>
      )}
      <div className="grid-2">
        <Card
          title={`Últimos ${meses} meses`}
          extra={
            <div className="tabs" role="tablist" aria-label="Meses en el gráfico">
              {([6, 12] as const).map((n) => <button key={n} className={meses === n ? "is-active" : ""} onClick={() => setMeses(n)}>{n} meses</button>)}
            </div>
          }
        >
          <MonthBars rows={s.historial.slice(-meses)} a="ganado" b="pagado" labelA="Ganado" labelB="Pagado" selected={mes} onPick={setMes} />
          <p className="com-note" style={{ marginTop: 6 }}>Tocá un mes para ver sus números y sus movimientos. Actualizado {parseTs(s.actualizado).toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit" })}</p>
        </Card>
        <Card title="Tu regla">
          {s.reglas.length === 0 ? <p className="muted" style={{ margin: 0 }}>Todavía no tiene una regla de comisión. Pedísela a administración.</p> : (
            <ul style={{ margin: 0, paddingLeft: 18 }}>{s.reglas.map((r) => <li key={r.id} style={{ marginBottom: 4 }}>{r.summary}{r.general ? <span className="muted"> · regla de todos los vendedores</span> : null}</li>)}</ul>
          )}
          <p className="com-note">Montos sin IVA. La comisión nace con el cobro (o con el evento de tu regla) y la ves desde ese momento, marcada «pendiente de aprobación» hasta que administración la apruebe.</p>
        </Card>
      </div>
      <Card title={`Mis movimientos · ${actual ? "este mes" : mesLargo(mes)}`} flush>
        {!rows ? <div style={{ padding: 24, textAlign: "center" }}><span className="spinner" /></div> : rows.rows.length === 0 ? <Empty title={actual ? "Todavía no hay comisiones este mes" : `Sin comisiones en ${mesLargo(mes)}`} hint="Se generan solas cuando entra el pago de tus facturas." /> : (
          <table className="table table--stack">
            <thead><tr><th>Fecha</th><th>Concepto</th><th>Documento</th><th className="num">Monto</th><th>Estado</th></tr></thead>
            <tbody>{rows.rows.map((r) => (
              <tr key={r.id}>
                <td className="muted" data-label="Fecha">{fmtDate(r.earned_on)}</td>
                <td className="stack-full" data-label="Concepto">{r.description || "Comisión"}{r.base === "margen" && r.base_amount == null ? <div className="meta">Sobre la utilidad (confidencial)</div> : null}{r.hold_reason ? <div className="meta">{r.hold_reason}</div> : null}</td>
                <td className="mono muted" data-label="Documento">{r.invoice_id ? <Link className="row-link" to={`/facturas/${r.invoice_id}`}>{r.invoice}</Link> : r.quote || r.project || "—"}</td>
                <td className={`num money ${Number(r.amount) < 0 ? "com-neg" : ""}`} style={{ fontWeight: 700 }} data-label="Monto">{fmtMoney(r.amount, r.currency)}</td>
                <td data-label="Estado"><span className={`badge badge--${ESTADO[r.status]?.tone || "muted"}`} title={r.status === "pendiente" ? "Ya la ganaste: falta que administración la apruebe" : undefined}>{r.status === "pendiente" ? "Pendiente de aprobación" : ESTADO[r.status]?.label || r.status}</span></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
    </>
  );
}
