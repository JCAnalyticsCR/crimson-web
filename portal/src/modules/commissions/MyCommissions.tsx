/* Mis comisiones (13/10, 34:35): "va a poder ver cuanto esta ganando en tiempo real". Lo ganado, lo que ganaria si le
   pagan sus facturas, lo del embudo, su regla y sus movimientos. Se actualiza al volver a la pestaña y cada minuto
   mientras esta visible. Montos sin IVA, en colones.
   26/10: la vendedora mira su avance POR SEMANA (lunes a domingo): arriba elige la semana (o el mes) con flechas, la
   cifra grande es lo ganado en esa semana contra la anterior, y el grafico muestra las ultimas semanas (tocar una barra
   la elige). Por cobrar y el embudo son siempre de hoy. En el celular los movimientos pasan a tarjetas. */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney, parseTs } from "../../lib/api";
import { Card, Empty, I, Icon } from "../../ui/components";
import MonthBars from "./MonthBars";
import { ESTADO, errText, type Ledger, type Summary } from "./types";

const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
const MES3 = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"];
const ATRAS = "M19 12H5|M11 6l-6 6 6 6";
type Modo = "semana" | "mes";

/* fechas como AAAA-MM-DD en hora de Costa Rica, sin depender de la zona del navegador */
const hoyCR = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Costa_Rica" });
const aFecha = (iso: string) => { const [y, m, d] = iso.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
const aIso = (f: Date) => f.toISOString().slice(0, 10);
const masDias = (iso: string, n: number) => { const f = aFecha(iso); f.setUTCDate(f.getUTCDate() + n); return aIso(f); };
const lunesDe = (iso: string) => masDias(iso, -((aFecha(iso).getUTCDay() + 6) % 7));
const dm = (iso: string) => { const f = aFecha(iso); return `${f.getUTCDate()} ${MES3[f.getUTCMonth()]}`; };
const mesLargo = (k: string) => { const [y, m] = k.split("-"); return `${MESES[Number(m) - 1] || m} ${y}`; };
const ultimoDia = (k: string) => { const [y, m] = k.split("-").map(Number); return new Date(Date.UTC(y, m, 0)).getUTCDate(); };
const masMeses = (k: string, n: number) => { const [y, m] = k.split("-").map(Number); return aIso(new Date(Date.UTC(y, m - 1 + n, 1))).slice(0, 7); };

const rangoDe = (modo: Modo, ancla: string) =>
  modo === "semana" ? { from: ancla, to: masDias(ancla, 6) } : { from: `${ancla}-01`, to: `${ancla}-${String(ultimoDia(ancla)).padStart(2, "0")}` };
const nombreSemana = (lunes: string) => {
  const dom = masDias(lunes, 6);
  return aFecha(lunes).getUTCMonth() === aFecha(dom).getUTCMonth() ? `${aFecha(lunes).getUTCDate()} al ${dm(dom)}` : `${dm(lunes)} al ${dm(dom)}`;
};

export default function MyCommissions() {
  const [s, setS] = useState<Summary | null>(null);
  const [rows, setRows] = useState<Ledger | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [modo, setModo] = useState<Modo>("semana");
  const [ancla, setAncla] = useState(() => lunesDe(hoyCR()));
  const hoy = hoyCR();
  const actual = modo === "semana" ? ancla === lunesDe(hoy) : ancla === hoy.slice(0, 7);
  const load = useCallback(() => {
    const { from, to } = rangoDe(modo, ancla);
    api<Summary>(`/commissions/summary?from=${from}&to=${to}`).then((x) => { setS(x); setErr(null); }).catch((e) => setErr(errText(e)));
    api<Ledger>(`/commissions?from=${from}&to=${to}&limit=100`).then(setRows).catch(() => setRows(null));
  }, [modo, ancla]);
  useEffect(() => {
    load();
    const t = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, 60000);
    const v = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", v);
    return () => { window.clearInterval(t); document.removeEventListener("visibilitychange", v); };
  }, [load]);

  const cambiarModo = (m: Modo) => { setModo(m); setAncla(m === "semana" ? lunesDe(hoyCR()) : hoyCR().slice(0, 7)); };
  const mover = (n: number) => setAncla((a) => (modo === "semana" ? masDias(a, 7 * n) : masMeses(a, n)));

  if (err) return <p style={{ color: "var(--bad)" }}>{err}</p>;
  if (!s) return <div style={{ padding: 40, textAlign: "center" }}><span className="spinner" /></div>;

  const sem = modo === "semana";
  const etiqueta = sem ? (actual ? "Esta semana" : `Semana del ${nombreSemana(ancla)}`) : actual ? `Este mes · ${mesLargo(ancla)}` : mesLargo(ancla);
  const enPeriodo = sem ? (actual ? "esta semana" : "esa semana") : actual ? "este mes" : `en ${mesLargo(ancla)}`;
  // contra el periodo anterior (las semanas vienen en el resumen; los meses en el historial)
  const serie = sem ? (s.semanas || []).map((w) => ({ k: w.semana, g: Number(w.ganado) })) : s.historial.map((h) => ({ k: h.mes, g: Number(h.ganado) }));
  const i = serie.findIndex((x) => x.k === ancla);
  const antes = i > 0 ? serie[i - 1].g : null;
  const ahora = Number(s.ganado.total);
  const delta = antes === null ? null : antes > 0 ? Math.round(((ahora - antes) / antes) * 100) : ahora > 0 ? Infinity : 0;
  const anterior = sem ? "la semana anterior" : "el mes anterior";
  const nCom = rows?.rows.length ?? 0;
  const angosto = typeof window !== "undefined" && window.innerWidth < 640;
  const barras = sem
    ? (s.semanas || []).slice(angosto ? -6 : -12).map((w) => ({ mes: w.semana, ganado: w.ganado, pagado: w.pagado }))
    : s.historial.slice(angosto ? -6 : -12);

  return (
    <div className="mc">
      <div className="mc-bar">
        <div className="tabs" role="tablist" aria-label="Ver por">
          {(["semana", "mes"] as const).map((m) => <button key={m} className={modo === m ? "is-active" : ""} onClick={() => cambiarModo(m)}>{m === "semana" ? "Por semana" : "Por mes"}</button>)}
        </div>
        <div className="mc-nav">
          <button type="button" className="btn btn--ghost btn--icon btn--sm" onClick={() => mover(-1)} aria-label={sem ? "Semana anterior" : "Mes anterior"}><Icon d={ATRAS} size={15} /></button>
          <b className="mc-nav__lbl">{etiqueta}</b>
          <button type="button" className="btn btn--ghost btn--icon btn--sm" onClick={() => mover(1)} disabled={actual} aria-label={sem ? "Semana siguiente" : "Mes siguiente"}><Icon d={I.arrow} size={15} /></button>
          {!actual && <button type="button" className="btn btn--ghost btn--sm" onClick={() => cambiarModo(modo)}>{sem ? "Volver a esta semana" : "Volver a este mes"}</button>}
        </div>
      </div>

      <section className="mc-hero" aria-label={`Ganado ${enPeriodo}`}>
        <div className="mc-hero__main">
          <span className="meta">Ganado {enPeriodo}</span>
          <b className="money mc-hero__num">{fmtMoney(s.ganado.total)}</b>
          {delta !== null && (
            <span className={`mc-delta${delta > 0 ? " is-up" : delta < 0 ? " is-down" : ""}`}>
              {delta === Infinity ? `Arriba de ${anterior} (₡0)` : delta === 0 ? `Igual que ${anterior}` : `${delta > 0 ? "▲" : "▼"} ${Math.abs(delta)} % contra ${anterior}`}
            </span>
          )}
        </div>
        <ul className="mc-hero__facts">
          <li><b>{nCom}</b><span>comisi{nCom === 1 ? "ón" : "ones"}</span></li>
          <li><b className="money">{fmtMoney(s.ganado.pendiente)}</b><span>por aprobar</span></li>
          <li><b className="money">{fmtMoney(s.pagado)}</b><span>pagado {enPeriodo}</span></li>
        </ul>
      </section>

      <div className="com-cards mc-cards">
        <div className="com-card is-warn"><span className="meta">Por cobrar · hoy</span><b className="money">{fmtMoney(s.por_cobrar.total)}</b><small>si te pagan tus {s.por_cobrar.facturas} factura{s.por_cobrar.facturas !== 1 ? "s" : ""} abierta{s.por_cobrar.facturas !== 1 ? "s" : ""}{s.por_cobrar.estimado ? " · estimado" : ""}</small></div>
        <div className="com-card"><span className="meta">En el embudo · hoy</span><b className="money">{fmtMoney(s.embudo.total)}</b><small>según la probabilidad de {s.embudo.oportunidades} oportunidad{s.embudo.oportunidades !== 1 ? "es" : ""}</small></div>
        {Number(s.si_aceptan.total) > 0 && <div className="com-card"><span className="meta">Si aceptan · hoy</span><b className="money">{fmtMoney(s.si_aceptan.total)}</b><small>{s.si_aceptan.cotizaciones} cotización(es) enviada(s)</small></div>}
        {Number(s.en_curso.total) > 0 && <div className="com-card"><span className="meta">Proyectos en curso</span><b className="money">{fmtMoney(s.en_curso.total)}</b><small>al completar {s.en_curso.proyectos}{s.en_curso.estimado ? " · estimado" : ""}</small></div>}
      </div>

      <Card title={sem ? "Tus semanas" : "Tus meses"}>
        <MonthBars rows={barras} a="ganado" b="pagado" labelA="Ganado" labelB="Pagado" selected={ancla} onPick={setAncla} lbl={sem ? dm : undefined} />
        <p className="com-note" style={{ marginTop: 6 }}>Tocá {sem ? "una semana" : "un mes"} para ver sus números y sus movimientos. Actualizado {parseTs(s.actualizado).toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit" })}</p>
      </Card>

      <Card title={`Mis movimientos · ${etiqueta.charAt(0).toLowerCase()}${etiqueta.slice(1)}`} flush>
        {!rows ? <div style={{ padding: 24, textAlign: "center" }}><span className="spinner" /></div> : rows.rows.length === 0 ? <Empty title={`Sin comisiones ${enPeriodo}`} hint="Se generan solas cuando entra el pago de tus facturas." /> : (
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

      <Card title="Tu regla">
        {s.reglas.length === 0 ? <p className="muted" style={{ margin: 0 }}>Todavía no tiene una regla de comisión. Pedísela a administración.</p> : (
          <ul style={{ margin: 0, paddingLeft: 18 }}>{s.reglas.map((r) => <li key={r.id} style={{ marginBottom: 4 }}>{r.summary}{r.general ? <span className="muted"> · regla de todos los vendedores</span> : null}</li>)}</ul>
        )}
        <p className="com-note">Montos sin IVA. La comisión nace con el cobro (o con el evento de tu regla) y la ves desde ese momento, marcada «pendiente de aprobación» hasta que administración la apruebe.</p>
      </Card>
    </div>
  );
}
