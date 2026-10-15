/* Mis ganancias (31:29-31:53): lo que la alianza le deja a lo largo del tiempo. Los montos los calcula economía
   (services/commissions: pagos acordados, comisiones, bonos); este frente suma la mensualidad de la plataforma y el
   neto. Un solo gráfico de magnitud (lo devengado por mes, un tono) y la tabla con el detalle. Sin IVA. */
import { useEffect, useState } from "react";
import { ApiError, api, fmtMoney } from "../../lib/api";
import { Empty } from "../../ui/components";
import { errorText } from "./shared";

type Mes = { mes: string; devengado: string; recibido: string; mensualidad: string; neto: string };
type G = { moneda?: string; totales: Record<string, string>; por_mes: Mes[]; proyectos?: { project_id: number; number?: string; name?: string; devengado?: string }[]; nota: string };

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"];
const mesLbl = (k: string) => { const [y, m] = k.split("-"); return `${MESES[Number(m) - 1] || m} ${y.slice(2)}`; };

export default function AllyEarnings() {
  const [g, setG] = useState<G | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api<G>("/aliado/ganancias").then(setG).catch((e) => setErr(e instanceof ApiError && e.status === 503 ? "Disponible pronto: Crimson está terminando el cálculo de lo que recibe cada aliado." : errorText(e)));
  }, []);
  if (err) return <><div className="cp-h"><h1 className="h1">Mis ganancias</h1></div><p className="muted">{err}</p></>;
  if (!g) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const mon = g.moneda || "CRC";
  const max = Math.max(...g.por_mes.map((m) => Number(m.devengado) || 0), 1);
  const t = g.totales;
  return (
    <>
      <div className="cp-h"><h1 className="h1">Mis ganancias</h1></div>
      <div className="cp-grid cp-grid--3 al-money" style={{ marginBottom: 16 }}>
        <div className="cp-stat"><span className="meta">Recibido</span><b>{fmtMoney(t.recibido, mon)}</b></div>
        <div className="cp-stat"><span className="meta">Por recibir</span><b>{fmtMoney(t.por_recibir, mon)}</b></div>
        <div className="cp-stat"><span className="meta">Neto después de la mensualidad</span><b>{fmtMoney(t.neto, mon)}</b><span className="muted" style={{ fontSize: 13 }}>Mensualidad del período: {fmtMoney(t.mensualidad, mon)}</span></div>
      </div>
      {g.por_mes.length ? (
        <div className="cp-card">
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
      <p className="meta" style={{ marginTop: 8 }}>{g.nota}</p>
    </>
  );
}
