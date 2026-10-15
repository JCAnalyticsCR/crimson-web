/* Lo que recibe un aliado (13/10, 26:36-27:23 y 31:29-31:49): recibido, por recibir, proyectado, 24 meses y cada
   proyecto en comun. Sirve para el admin (Comisiones -> Aliados, base "/commissions/earnings?ally_company_id=") y
   para el portal del aliado (base "/aliado/ganancias"). Nunca muestra el precio de venta ni los costos de Crimson. */
import { useEffect, useState } from "react";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { Card, Empty, Modal } from "../../ui/components";
import MonthBars from "./MonthBars";
import { ESTADO, errText, type AllyEarningsData, type AllyProjectDetail } from "./types";

export default function AllyEarnings({ base, projectBase }: { base: string; projectBase: (pid: number) => string }) {
  const [g, setG] = useState<AllyEarningsData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [det, setDet] = useState<AllyProjectDetail | null>(null);
  useEffect(() => { setG(null); api<AllyEarningsData>(base).then(setG).catch((e) => setErr(errText(e))); }, [base]);
  const abrir = (pid: number) => api<AllyProjectDetail>(projectBase(pid)).then(setDet).catch((e) => setErr(errText(e)));
  if (err) return <p style={{ color: "var(--bad)" }}>{err}</p>;
  if (!g) return <div style={{ padding: 40, textAlign: "center" }}><span className="spinner" /></div>;
  const t = g.totales;
  const mon = g.moneda || "CRC";
  return (
    <>
      <div className="com-cards">
        <div className="com-card is-good"><span className="meta">Recibido</span><b className="money">{fmtMoney(t.recibido, mon)}</b></div>
        <div className="com-card is-warn"><span className="meta">Por recibir</span><b className="money">{fmtMoney(t.por_recibir, mon)}</b>{t.en_revision ? <small>{t.en_revision} en revisión</small> : null}</div>
        <div className="com-card"><span className="meta">Proyectado</span><b className="money">{fmtMoney(t.proyectado, mon)}</b><small>proyectos en curso, precios aprobados y bonos posibles</small></div>
        <div className="com-card"><span className="meta">Proyectos en común</span><b>{t.proyectos ?? g.proyectos.length}</b>{t.promedio_por_proyecto ? <small>promedio {fmtMoney(t.promedio_por_proyecto, mon)} por proyecto</small> : null}</div>
        {t.ingreso_por_hora ? <div className="com-card"><span className="meta">₡ por hora de sus técnicos</span><b className="money">{fmtMoney(t.ingreso_por_hora, mon)}</b><small>{t.horas} h de campo</small></div> : null}
      </div>
      {t.dias_promedio_pago != null && <p className="muted" style={{ marginTop: -6, fontSize: 13 }}>Crimson paga en promedio en {t.dias_promedio_pago} días.</p>}
      <Card title="Mes a mes">
        {g.por_mes.length ? <MonthBars rows={g.por_mes} a="devengado" b="pagado" labelA="Devengado" labelB="Pagado" currency={mon} /> : <p className="muted">Sin meses.</p>}
      </Card>
      <Card title="Por proyecto" flush>
        {g.proyectos.length === 0 ? <Empty title="Todavía no hay proyectos en común" /> : (
          <div className="com-scroll">
            <table className="table">
              <thead><tr><th>Proyecto</th><th>Cliente</th><th>Estado</th><th>Lo que pusiste</th><th className="num">Comisión</th><th className="num">Bonos</th><th className="num">Total</th><th className="num">Pagado</th><th className="num">Por recibir</th><th /></tr></thead>
              <tbody>{g.proyectos.map((p) => (
                <tr key={p.project_id}>
                  <td style={{ fontWeight: 600 }}>{p.number}<div className="muted" style={{ fontSize: 12 }}>{p.name}</div>{p.bono_anticipo_posible && <div className="meta" style={{ color: "var(--ok)" }}>Terminá antes del {fmtDate(p.bono_anticipo_posible.fecha)} y ganás {fmtMoney(p.bono_anticipo_posible.monto, p.bono_anticipo_posible.moneda)} más</div>}</td>
                  <td>{p.cliente || "—"}</td>
                  <td><span className={`badge badge--${p.completed_at ? "ok" : "warn"}`}>{p.completed_at ? "Completado" : "En curso"}</span></td>
                  <td>{p.lo_que_pusiste.length ? p.lo_que_pusiste.map((x, i) => <div key={i} style={{ fontSize: 12.5 }}>{x.what}: {x.amount != null ? fmtMoney(x.amount, x.currency) : "—"}{x.status !== "aprobado" ? " · en revisión" : ""}</div>) : "—"}</td>
                  <td className="num money">{fmtMoney(p.comision, mon)}</td>
                  <td className="num money">{fmtMoney(p.bonos, mon)}</td>
                  <td className="num money" style={{ fontWeight: 700 }}>{fmtMoney(p.total, mon)}</td>
                  <td className="num money">{fmtMoney(p.pagado, mon)}</td>
                  <td className="num money">{fmtMoney(p.por_recibir, mon)}</td>
                  <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => abrir(p.project_id)}>Detalle</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      <p className="com-note">{g.nota || "Montos sin IVA. Para cobrar, enviá tu factura electrónica a Crimson por el monto aprobado."}</p>
      {det && (
        <Modal wide title={det.project ? `${det.project.number} · ${det.project.name}` : "Proyecto"} onClose={() => setDet(null)} foot={<button className="btn btn--ghost" onClick={() => setDet(null)}>Cerrar</button>}>
          <div className="com-cards">
            <div className="com-card"><span className="meta">Recibido</span><b className="money">{fmtMoney(det.totales.recibido)}</b></div>
            <div className="com-card"><span className="meta">Por recibir</span><b className="money">{fmtMoney(det.totales.por_recibir)}</b></div>
            <div className="com-card"><span className="meta">Proyectado</span><b className="money">{fmtMoney(det.totales.proyectado)}</b></div>
          </div>
          {det.bono_anticipo && <p className="muted">Bono por terminar antes del {fmtDate(det.bono_anticipo.fecha)}: {fmtMoney(det.bono_anticipo.monto, det.bono_anticipo.moneda)}{det.bono_anticipo.posible ? "" : " (ya no aplica)"}</p>}
          <b>Tus precios</b>
          {det.lo_que_pusiste.length ? <ul style={{ margin: "4px 0 12px", paddingLeft: 18 }}>{det.lo_que_pusiste.map((x, i) => <li key={i}>{x.what}: {x.amount != null ? fmtMoney(x.amount, x.currency) : "—"} · {x.status === "aprobado" ? "aprobado" : "en revisión"}</li>)}</ul> : <p className="muted">Sin precios aprobados.</p>}
          <div className="com-scroll">
            <table className="table">
              <thead><tr><th>Fecha</th><th>Tipo</th><th>Concepto</th><th className="num">Monto</th><th>Estado</th></tr></thead>
              <tbody>{det.movimientos.map((m) => (
                <tr key={m.id}><td className="muted">{fmtDate(m.earned_on)}</td><td>{m.kind_label}</td><td>{m.description}</td><td className={`num money ${Number(m.amount) < 0 ? "com-neg" : ""}`}>{fmtMoney(m.amount, m.currency)}</td><td><span className={`badge badge--${ESTADO[m.status]?.tone || "muted"}`}>{ESTADO[m.status]?.label || m.status}</span></td></tr>
              ))}</tbody>
            </table>
          </div>
        </Modal>
      )}
    </>
  );
}
