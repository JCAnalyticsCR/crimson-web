/* Reportes de combustible: compras por mes (con meses parciales), litros, costo por litro, distribucion por
   vehiculo, km validos, km/L y costo por km. La referencia mensual es un parametro NO validado. */
import { useEffect, useState } from "react";
import { api, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, I, Icon, Loading, Modal, Field } from "../../ui/components";
import AuthLink from "../../ui/AuthLink";
import { type Lookups, type Report, fmtNum, monthLabel, n, today, yearStart } from "./types";

function MonthChart({ rep }: { rep: Report }) {
  const ref = n(rep.reference.amount) || 0;
  const vals = rep.months.map((m) => n(m.amount) || 0);
  const max = Math.max(ref, ...vals, 1) * 1.12;
  const W = 640, H = 220, L = 8, B = 28, T = 14;
  const bw = (W - L * 2) / Math.max(rep.months.length, 1);
  const y = (v: number) => T + (H - T - B) * (1 - v / max);
  return (
    <>
      <svg className="veh-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Compras de combustible por mes">
        <defs>
          <pattern id="veh-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="var(--crimson-soft)" /><line x1="0" y1="0" x2="0" y2="6" stroke="var(--crimson)" strokeWidth="2" opacity=".5" />
          </pattern>
        </defs>
        <line x1={L} x2={W - L} y1={H - B} y2={H - B} stroke="var(--hair-2)" />
        {rep.months.map((m, i) => {
          const v = n(m.amount) || 0;
          const x = L + i * bw + bw * 0.18;
          const w = bw * 0.64;
          return (
            <g key={m.month}>
              <title>{`${monthLabel(m.month)}: ${fmtMoney(v)}${m.partial ? ` · parcial (${m.partial_reason})` : ""}`}</title>
              <rect className={`bar ${m.partial ? "bar--partial" : ""}`} x={x} y={y(v)} width={w} height={Math.max(H - B - y(v), v ? 1 : 0)} rx="3" />
              <text x={x + w / 2} y={H - B + 14} textAnchor="middle">{monthLabel(m.month)}</text>
              {m.partial && rep.months.length <= 14 && <text x={x + w / 2} y={H - B + 25} textAnchor="middle">parcial</text>}
            </g>
          );
        })}
        {ref > 0 && (
          <g>
            <line className="ref" x1={L} x2={W - L} y1={y(ref)} y2={y(ref)} />
            <text className="ref-label" x={W - L} y={y(ref) - 5} textAnchor="end">referencia no validada · {fmtMoney(ref)}</text>
          </g>
        )}
      </svg>
      <div className="veh-legend">
        <span><i style={{ background: "var(--crimson)" }} />Compras del mes</span>
        <span><i style={{ background: "var(--crimson-soft)", border: "1px solid var(--crimson)" }} />Mes parcial</span>
        <span><i style={{ background: "transparent", borderTop: "2px dashed var(--info)", borderRadius: 0, height: 0 }} />Referencia histórica (parámetro, no promedio)</span>
      </div>
    </>
  );
}

export default function ReportView({ lookups, refresh }: { lookups: Lookups | null; refresh: number }) {
  const { allows, toast } = useSession();
  const [range, setRange] = useState({ from: yearStart(), to: today(), vehicle: "" });
  const [rep, setRep] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [refEdit, setRefEdit] = useState<string | null>(null);
  const qs = `from=${range.from}&to=${range.to}${range.vehicle ? `&vehicle_id=${range.vehicle}` : ""}`;
  const load = () => { setBusy(true); api<Report>(`/vehicles/report?${qs}`).then(setRep).catch((e) => toast(e instanceof Error ? e.message : "Error", "bad")).finally(() => setBusy(false)); };
  useEffect(load, [qs, refresh]); // eslint-disable-line react-hooks/exhaustive-deps
  const saveRef = async () => {
    if (refEdit === null) return;
    try { await api("/vehicles/config", { method: "PUT", json: { monthly_reference: Number(refEdit || 0) } }); toast("Referencia actualizada"); setRefEdit(null); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const t = rep?.totals;
  return (
    <div className="veh-stack">
      <div className="veh-filters">
        <div className="field"><label>Desde</label><input className="input" type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></div>
        <div className="field"><label>Hasta</label><input className="input" type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></div>
        <div className="field field--grow"><label>Vehículo</label>
          <select className="select" value={range.vehicle} onChange={(e) => setRange({ ...range, vehicle: e.target.value })}>
            <option value="">Todos</option>
            {lookups?.vehicles.map((v) => <option key={v.id} value={v.id}>{v.name}{v.plate ? ` · ${v.plate}` : ""}{v.excluded ? " (excluido)" : ""}</option>)}
          </select>
        </div>
        {allows("vehicles.exportar") && <AuthLink className="btn btn--crimson" path={`/vehicles/report?${qs}&format=xlsx`} download={`vehiculos-${range.from}-${range.to}.xlsx`}><Icon d={I.reports} />Excel</AuthLink>}
        {busy && <span className="spinner" style={{ alignSelf: "center" }} />}
      </div>
      <div className="veh-note"><Icon d={I.bell} size={16} /><span><b>{rep?.note || "Comprar en un mes no equivale a consumir en ese mes."}</b> Los montos por mes son compras de combustible; el consumo real solo se mide con km/L entre tanques llenos.</span></div>
      {!rep ? <Loading /> : (
        <>
          <div className="veh-kpis">
            <div className="veh-kpi"><span>Compras del periodo</span><b className="money">{fmtMoney(t?.amount)}</b><small>{t?.loads} cargas en análisis</small></div>
            <div className="veh-kpi"><span>Litros</span><b>{fmtNum(t?.liters, 1)} L</b><small>{t?.cost_per_liter != null ? `${fmtMoney(t.cost_per_liter)} por litro` : "sin litros registrados"}</small></div>
            <div className="veh-kpi"><span>Km válidos</span><b>{fmtNum(t?.km_valid)} km</b><small>entre tanques llenos coherentes</small></div>
            <div className="veh-kpi"><span>Rendimiento</span><b>{t?.km_per_l != null ? `${fmtNum(t.km_per_l, 1)} km/L` : "—"}</b><small>{t?.cost_per_km != null ? `${fmtMoney(t.cost_per_km)} por km` : "falta un intervalo válido"}</small></div>
            <div className="veh-kpi"><span>Fuera del análisis</span><b className="money">{fmtMoney(t?.excluded_amount)}</b><small>{t?.excluded_loads} cargas excluidas (se ven en la lista)</small></div>
            <div className="veh-kpi veh-kpi--ref">
              <span>Referencia mensual</span><b className="money">{fmtMoney(rep.reference.amount)}</b>
              <small><span className="veh-tag veh-tag--warn">no validada</span> parámetro, no es un promedio{allows("vehicles.editar") && <> · <button className="btn btn--ghost btn--sm" style={{ padding: "0 6px", minHeight: 0 }} onClick={() => setRefEdit(String(n(rep.reference.amount) ?? ""))}>cambiar</button></>}</small>
            </div>
          </div>
          {rep.warnings.length > 0 && <Card title="Avisos"><ul className="veh-warn">{rep.warnings.map((w) => <li key={w}>{w}</li>)}</ul></Card>}
          <Card title="Compras por mes" extra={<span className="meta">{rep.coverage.first ? `datos del ${rep.coverage.first} al ${rep.coverage.last}` : "sin datos"}</span>}>
            {rep.months.every((m) => !m.loads) ? <Empty hint="No hay cargas en análisis en el periodo." /> : <MonthChart rep={rep} />}
            <div className="veh-scroll" style={{ marginTop: 8 }}>
              <table className="table">
                <thead><tr><th>Mes</th><th className="num">Cargas</th><th className="num">Litros</th><th className="num">Compras</th><th className="num">₡ / litro</th><th>Cobertura</th></tr></thead>
                <tbody>{rep.months.map((m) => (
                  <tr key={m.month}>
                    <td>{monthLabel(m.month)}</td><td className="num mono">{m.loads}</td><td className="num mono">{fmtNum(m.liters, 1)}{m.liters_missing ? <span className="meta"> (+{m.liters_missing} sin L)</span> : null}</td>
                    <td className="num money">{fmtMoney(m.amount)}</td><td className="num money">{m.cost_per_liter != null ? fmtMoney(m.cost_per_liter) : "—"}</td>
                    <td>{m.partial ? <span className="veh-tag veh-tag--warn" title={m.partial_reason || ""}>parcial · {m.partial_reason}</span> : <span className="veh-tag veh-tag--muted">mes completo</span>}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </Card>
          <Card title="Distribución por vehículo" flush>
            {rep.by_vehicle.length === 0 ? <Empty hint="Registrá vehículos y cargas para ver la distribución." /> : (
              <div className="veh-scroll"><table className="table">
                <thead><tr><th>Vehículo</th><th className="num">Cargas</th><th className="num">Compras</th><th>Participación</th><th className="num">₡ / litro</th><th className="num">Km válidos</th><th className="num">Km/L</th><th className="num">₡ / km</th><th className="num">Otros costos</th></tr></thead>
                <tbody>{rep.by_vehicle.map((v) => (
                  <tr key={v.vehicle_id} className={v.excluded ? "veh-row--off" : ""}>
                    <td style={{ minWidth: 180 }}><b>{v.name}</b>{v.plate && <span className="meta"> · {v.plate}</span>}{v.owner === "personal" && <span className="meta"> · personal</span>}
                      {v.excluded && <div className="veh-why"><span className="veh-tag veh-tag--off">Excluido</span> {v.exclusion_reason} · {fmtMoney(v.amount_excluded)} fuera del análisis</div>}</td>
                    <td className="num mono">{v.loads}{v.loads_excluded ? <span className="meta"> (+{v.loads_excluded} excl.)</span> : null}</td>
                    <td className="num money">{fmtMoney(v.amount)}</td>
                    <td>{v.share_pct != null ? <div className="veh-share"><i style={{ width: `${Math.max(2, v.share_pct)}%` }} /><span className="mono">{v.share_pct.toFixed(1)}%</span></div> : "—"}</td>
                    <td className="num money">{v.cost_per_liter != null ? fmtMoney(v.cost_per_liter) : "—"}</td>
                    <td className="num mono">{fmtNum(v.km_valid)}<div className="meta">{v.intervals_valid}/{v.intervals} intervalos</div></td>
                    <td className="num mono">{v.km_per_l != null ? fmtNum(v.km_per_l, 1) : "—"}</td>
                    <td className="num money">{v.cost_per_km != null ? fmtMoney(v.cost_per_km) : "—"}</td>
                    <td className="num money" title={Object.entries(v.other_costs).map(([k, x]) => `${k}: ${fmtMoney(x)}`).join(" · ")}>{n(v.other_costs_total) ? fmtMoney(v.other_costs_total) : "—"}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
          </Card>
          <Card title="Intervalos entre tanques llenos (km/L)" flush extra={<span className="meta">solo los válidos cuentan</span>}>
            {rep.intervals.length === 0 ? <Empty title="Sin intervalos todavía" hint="Hacen falta dos cargas con tanque lleno y odómetro del mismo vehículo." /> : (
              <div className="veh-scroll"><table className="table">
                <thead><tr><th>Vehículo</th><th>Desde → hasta</th><th className="num">Odómetro</th><th className="num">Km</th><th className="num">Litros</th><th className="num">Km/L</th><th>Estado</th></tr></thead>
                <tbody>{rep.intervals.map((i, k) => (
                  <tr key={k} className={i.valid ? "" : "veh-row--off"}>
                    <td>{i.vehicle}</td><td className="mono veh-nowrap">{i.from} → {i.to}</td><td className="num mono veh-nowrap">{fmtNum(i.odo_from)} → {fmtNum(i.odo_to)}</td>
                    <td className="num mono">{fmtNum(i.km)}</td><td className="num mono">{fmtNum(i.liters, 1)}</td><td className="num mono">{i.km_per_l != null ? fmtNum(i.km_per_l, 1) : "—"}</td>
                    <td className="veh-why">{i.valid ? <span className="veh-tag veh-tag--ok">válido</span> : <><span className="veh-tag veh-tag--off">descartado</span> {i.reason}</>}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
          </Card>
        </>
      )}
      {refEdit !== null && (
        <Modal title="Referencia histórica mensual" onClose={() => setRefEdit(null)} foot={<><button className="btn btn--ghost" onClick={() => setRefEdit(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveRef}>Guardar</button></>}>
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>Es el monto que se mencionaba como gasto mensual "de siempre". Se muestra como referencia <b>no validada</b>: nunca reemplaza lo que dicen las cargas registradas.</p>
          <Field label="Monto mensual (₡)"><input className="input input--mono" inputMode="numeric" value={refEdit} onChange={(e) => setRefEdit(e.target.value.replace(/[^\d.]/g, ""))} /></Field>
        </Modal>
      )}
    </div>
  );
}
