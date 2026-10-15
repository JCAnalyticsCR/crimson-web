/* Mis ganancias (spec economia 9.8; 26:36-27:23 y 31:29-31:53): lo que la alianza le deja a lo largo del tiempo.
   Los montos los calcula economía (services/commissions: pagos acordados, comisiones, bonos) y solo de lo que
   Crimson le comparte; este frente suma la mensualidad de la plataforma y el neto. Tarjetas, 24 meses, la tabla por
   proyecto con "lo que pusiste" y el detalle de cada uno (/aliado/ganancias/proyectos/{id}). Sin IVA. */
import { useEffect, useState } from "react";
import { ApiError, api, fmtMoney } from "../../lib/api";
import { Empty } from "../../ui/components";
import { AllyProjectsCard, AllyStatCards } from "../commissions/AllyEarnings";
import type { AllyEarningsData } from "../commissions/types";
import "../commissions/commissions.css";
import { errorText } from "./shared";

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"];
const mesLbl = (k: string) => { const [y, m] = k.split("-"); return `${MESES[Number(m) - 1] || m} ${y.slice(2)}`; };

export default function AllyEarnings() {
  const [g, setG] = useState<AllyEarningsData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api<AllyEarningsData>("/aliado/ganancias").then(setG).catch((e) => setErr(e instanceof ApiError && e.status === 503 ? "Disponible pronto: Crimson está terminando el cálculo de lo que recibe cada aliado." : errorText(e)));
  }, []);
  if (err) return <><div className="cp-h"><h1 className="h1">Mis ganancias</h1></div><p className="muted">{err}</p></>;
  if (!g) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const mon = g.moneda || "CRC";
  const max = Math.max(...g.por_mes.map((m) => Number(m.devengado) || 0), 1);
  const t = g.totales;
  return (
    <>
      <div className="cp-h"><h1 className="h1">Mis ganancias</h1></div>
      <AllyStatCards g={g} tu="tus" />
      <div className="cp-grid cp-grid--3 al-money" style={{ marginBottom: 16 }}>
        <div className="cp-stat"><span className="meta">Devengado en el período</span><b>{fmtMoney(t.devengado, mon)}</b></div>
        <div className="cp-stat"><span className="meta">Mensualidad de la plataforma</span><b>{fmtMoney(t.mensualidad, mon)}</b></div>
        <div className="cp-stat"><span className="meta">Neto después de la mensualidad</span><b>{fmtMoney(t.neto, mon)}</b></div>
      </div>
      {g.por_mes.length ? (
        <div className="cp-card" style={{ marginBottom: 16 }}>
          <b>Lo devengado por mes</b>
          <div className="al-chart" role="img" aria-label="Gráfico de lo devengado por mes; el detalle está en la tabla de abajo">
            {g.por_mes.map((m) => (
              <div key={m.mes} className="al-chart__col" title={`${mesLbl(m.mes)}: devengado ${fmtMoney(m.devengado, mon)} · mensualidad ${fmtMoney(m.mensualidad, mon)} · neto ${fmtMoney(m.neto, mon)}`}>
                <div className="al-chart__bar" style={{ height: `${Math.max(2, ((Number(m.devengado) || 0) / max) * 110)}px` }} />
                <span className="al-chart__lbl">{mesLbl(m.mes)}</span>
              </div>
            ))}
          </div>
          <div className="al-table">
            <table>
              <thead><tr><th>Mes</th><th className="num">Devengado</th><th className="num">Recibido</th><th className="num">Mensualidad</th><th className="num">Neto</th></tr></thead>
              <tbody>
                {g.por_mes.map((m) => (
                  <tr key={m.mes}><td>{mesLbl(m.mes)}</td><td className="num">{fmtMoney(m.devengado, mon)}</td><td className="num">{fmtMoney(m.recibido, mon)}</td><td className="num">{fmtMoney(m.mensualidad, mon)}</td><td className="num"><b>{fmtMoney(m.neto, mon)}</b></td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : <Empty title="Todavía no hay movimientos" />}
      <AllyProjectsCard g={g} projectBase={(pid) => `/aliado/ganancias/proyectos/${pid}`} onError={setErr} />
      <p className="meta" style={{ marginTop: 8 }}>{g.nota || "Montos sin IVA. Para cobrar, enviá tu factura electrónica a Crimson por el monto aprobado."}</p>
    </>
  );
}
