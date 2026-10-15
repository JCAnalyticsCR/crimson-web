/* Mis numeros (13/10, 31:49: "y yo tambien poder ver mis numeros"): cobrado, facturado, proyectos cerrados con su
   costo directo, comisiones y lo que dejo cada vendedor y cada alianza. Solo quien ve costos. En colones. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtMoney } from "../../lib/api";
import { Card } from "../../ui/components";
import MonthBars from "./MonthBars";
import { errText, mesLbl, type Numbers as N } from "./types";

const hoy = () => new Date(new Date().toLocaleString("en-US", { timeZone: "America/Costa_Rica" }));
const iso = (d: Date) => d.toLocaleDateString("en-CA");
function periodo(k: string): { from: string; to: string } {
  const t = hoy();
  if (k === "mes") return { from: iso(new Date(t.getFullYear(), t.getMonth(), 1)), to: iso(t) };
  if (k === "trimestre") return { from: iso(new Date(t.getFullYear(), Math.floor(t.getMonth() / 3) * 3, 1)), to: iso(t) };
  if (k === "anio") return { from: iso(new Date(t.getFullYear(), 0, 1)), to: iso(t) };
  return { from: iso(new Date(t.getFullYear() - 1, t.getMonth(), 1)), to: iso(t) };
}

export default function Numbers({ allyId }: { allyId?: number }) {
  const [k, setK] = useState("12m");
  const [n, setN] = useState<N | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { const p = periodo(k); setN(null); api<N>(`/commissions/numbers?from=${p.from}&to=${p.to}`).then(setN).catch((e) => setErr(errText(e))); }, [k]);
  if (err) return <p style={{ color: "var(--bad)" }}>{err}</p>;
  if (!n) return <div style={{ padding: 40, textAlign: "center" }}><span className="spinner" /></div>;
  if (allyId) {
    const f = n.por_aliado.find((x) => x.ally_company_id === allyId);
    return (
      <Card title="Lo que dejó la alianza a Crimson" extra={<span className="meta">últimos 12 meses</span>}>
        {!f ? <p className="muted" style={{ margin: 0 }}>Sin proyectos completados con este aliado en el periodo.</p> : (
          <div className="com-cards" style={{ marginBottom: 0 }}>
            <div className="com-card"><span className="meta">Venta de proyectos · {f.proyectos}</span><b className="money">{fmtMoney(f.venta_proyectos)}</b></div>
            <div className="com-card"><span className="meta">Pagado al aliado</span><b className="money">{fmtMoney(f.pagado_al_aliado)}</b></div>
            <div className="com-card"><span className="meta">Comisiones y bonos</span><b className="money">{fmtMoney(f.comisiones_bonos)}</b></div>
            <div className={`com-card ${Number(f.utilidad_crimson) >= 0 ? "is-good" : ""}`}><span className="meta">Utilidad para Crimson · {f.margen.toFixed(1)} %</span><b className="money">{fmtMoney(f.utilidad_crimson)}</b></div>
          </div>
        )}
      </Card>
    );
  }
  const t = n.totales;
  return (
    <>
      <div className="tabs com-tabs" role="tablist">{[["mes", "Este mes"], ["trimestre", "Trimestre"], ["anio", "Este año"], ["12m", "Últimos 12 meses"]].map(([key, l]) => <button key={key} className={k === key ? "is-active" : ""} onClick={() => setK(key)}>{l}</button>)}</div>
      <div className="com-cards">
        <div className="com-card"><span className="meta">Cobrado sin IVA</span><b className="money">{fmtMoney(t.cobrado_sin_iva)}</b></div>
        <div className="com-card"><span className="meta">Venta de proyectos cerrados · {t.proyectos_cerrados}</span><b className="money">{fmtMoney(t.venta_proyectos)}</b>{t.proyectos_sin_costos ? <small>{t.proyectos_sin_costos} sin costos (fuera)</small> : null}</div>
        <div className="com-card"><span className="meta">Comisiones (vendedores y aliados)</span><b className="money">{fmtMoney(Number(t.comisiones_vendedores) + Number(t.comisiones_aliados))}</b></div>
        <div className={`com-card ${Number(t.utilidad_despues_comisiones) >= 0 ? "is-good" : ""}`}><span className="meta">Utilidad después de comisiones · {t.margen_neto.toFixed(1)} %</span><b className="money">{fmtMoney(t.utilidad_despues_comisiones)}</b></div>
      </div>
      <Card title="Venta de proyectos contra utilidad después de comisiones">
        <MonthBars rows={n.meses as unknown as { mes: string }[]} a="venta_proyectos" b="utilidad_despues_comisiones" labelA="Venta de proyectos" labelB="Utilidad después de comisiones" />
      </Card>
      <Card title="Mes a mes" flush>
        <div className="com-scroll">
          <table className="table">
            <thead><tr><th>Mes</th><th className="num">Cobrado</th><th className="num">Facturado</th><th className="num">Proyectos</th><th className="num">Venta proyectos</th><th className="num">Costo directo</th><th className="num">Utilidad bruta</th><th className="num">Com. vendedores</th><th className="num">Com. aliados</th><th className="num">Pagos a aliados</th><th className="num">Utilidad neta</th><th className="num">Margen</th></tr></thead>
            <tbody>
              {[...n.meses, { ...t, mes: "Total" }].map((m) => (
                <tr key={m.mes} style={m.mes === "Total" ? { fontWeight: 700 } : undefined}>
                  <td>{m.mes === "Total" ? "Total" : mesLbl(m.mes || "")}</td>
                  <td className="num money">{fmtMoney(m.cobrado_sin_iva)}</td><td className="num money">{fmtMoney(m.facturado_sin_iva)}</td>
                  <td className="num">{m.proyectos_cerrados}{m.proyectos_sin_costos ? ` (+${m.proyectos_sin_costos})` : ""}</td>
                  <td className="num money">{fmtMoney(m.venta_proyectos)}</td><td className="num money">{fmtMoney(m.costo_directo)}</td><td className="num money">{fmtMoney(m.utilidad_bruta)}</td>
                  <td className="num money">{fmtMoney(m.comisiones_vendedores)}</td><td className="num money">{fmtMoney(m.comisiones_aliados)}</td><td className="num money muted">{fmtMoney(m.pagos_aliados)}</td>
                  <td className="num money">{fmtMoney(m.utilidad_despues_comisiones)}</td><td className="num">{m.margen_neto.toFixed(1)} %</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <div className="grid-2">
        <Card title="Por vendedor" flush>
          {n.por_vendedor.length === 0 ? <p className="muted" style={{ padding: "0 18px 14px", fontSize: 13 }}>Sin ventas con vendedor en el periodo.</p> : (
            <div className="com-scroll"><table className="table"><thead><tr><th>Vendedor</th><th className="num">Facturado</th><th className="num">Cobrado</th><th className="num">Comisiones</th><th className="num">Proyectos</th></tr></thead>
              <tbody>{n.por_vendedor.map((v) => <tr key={v.user_id}><td style={{ fontWeight: 600 }}>{v.name || "—"}</td><td className="num money">{fmtMoney(v.facturado_sin_iva)}</td><td className="num money">{fmtMoney(v.cobrado_sin_iva)}</td><td className="num money">{fmtMoney(v.comisiones)}</td><td className="num">{v.proyectos_cerrados}</td></tr>)}</tbody></table></div>
          )}
        </Card>
        <Card title="Por aliado" flush>
          {n.por_aliado.length === 0 ? <p className="muted" style={{ padding: "0 18px 14px", fontSize: 13 }}>Sin proyectos con aliados en el periodo.</p> : (
            <div className="com-scroll"><table className="table"><thead><tr><th>Aliado</th><th className="num">Proyectos</th><th className="num">Venta</th><th className="num">Pagado al aliado</th><th className="num">Comisiones y bonos</th><th className="num">Utilidad Crimson</th><th className="num">Margen</th></tr></thead>
              <tbody>{n.por_aliado.map((a) => <tr key={a.ally_company_id}><td style={{ fontWeight: 600 }}>{a.name || "—"}</td><td className="num">{a.proyectos}</td><td className="num money">{fmtMoney(a.venta_proyectos)}</td><td className="num money">{fmtMoney(a.pagado_al_aliado)}</td><td className="num money">{fmtMoney(a.comisiones_bonos)}</td><td className="num money">{fmtMoney(a.utilidad_crimson)}</td><td className="num">{a.margen.toFixed(1)} %</td></tr>)}</tbody></table></div>
          )}
        </Card>
      </div>
      <p className="com-note">Montos sin IVA, en colones. No incluye gastos generales: ver <Link to="/reportes">Reportes → Estado de resultados</Link> y la rentabilidad por proyecto.</p>
    </>
  );
}
