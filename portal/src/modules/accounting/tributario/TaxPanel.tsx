/* Panel tributario del mes: IVA en colones (debito, credito de facturas electronicas y su parte "por confirmar",
   credito de gastos sin factura vinculada, excluido por rechazo, prorrata, a pagar), por responder, rechazos y errores,
   estado de la conexion con Hacienda y los reportes del mes. Sin costos ni margenes. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney, parseTs } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Card, Empty, Loading } from "../../../ui/components";
import AuthLink from "../../../ui/AuthLink";
import { MODE_LABEL, plazo, type Mode } from "../hacienda";

type Tax = {
  month: string;
  iva: { debito_crc: string; credito_xml_crc: string; credito_por_confirmar_crc: string; credito_sin_respaldo_crc: string; credito_manual_crc: string; credito_crc: string; proporcional_crc: string; prorrata_pct: string | null; credito_prorrata_crc: string; a_pagar_crc: string };
  credito_por_confirmar: { id: number; issuer_name: string | null; clave: string; credit_crc: string }[];
  recepciones: { recibidas: number; por_revisar: number; vencen_pronto: { id: number; issuer_name: string | null; total: string; currency: string; deadline: string | null; business_days_left: number | null }[]; vencidas: number; respondidas: number; total_crc: string };
  mensajes: { rechazados: { id: number; issuer_name: string | null; detail: string | null }[]; errores: number; por_enviar: number };
  emision: { pendientes: number; rechazadas: number; aceptadas: number };
  hacienda: { mode: Mode; environment: string; cert_not_after: string | null; cert_days_left: number | null; imap_last_run_at: string | null };
};

const REPORTS: [string, string][] = [["iva", "IVA (borrador D-104)"], ["d151", "D-270 / D-151 (sin comprobante electrónico)"], ["recepciones", "Recepciones"], ["compras", "Libro de compras"], ["gastos", "Gastos"]];

export default function TaxPanel() {
  const { toast, allows } = useSession();
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [t, setT] = useState<Tax | null>(null);
  const [pct, setPct] = useState("");
  const load = () => api<Tax>(`/accounting/tax-summary?month=${month}`).then((r) => { setT(r); setPct(r.iva.prorrata_pct === null ? "" : String(Number(r.iva.prorrata_pct))); }).catch((e) => toast(e.message, "bad"));
  useEffect(() => { setT(null); load(); }, [month]); // eslint-disable-line react-hooks/exhaustive-deps
  const savePct = async () => {
    try { await api("/accounting/settings", { method: "PUT", json: { iva_prorrata_pct: pct === "" ? null : Number(pct) } }); toast("Prorrata guardada"); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const [y, m] = month.split("-").map(Number);
  const from = `${month}-01`, to = new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10);
  const row = (label: string, v: string, opts: { strong?: boolean; warn?: boolean; muted?: boolean; minus?: boolean } = {}) => (
    <tr style={{ background: opts.warn ? "var(--warn-soft)" : undefined }}><td style={{ fontWeight: opts.strong ? 700 : undefined, color: opts.muted ? "var(--text-3)" : undefined, paddingLeft: opts.muted ? 26 : undefined }}>{label}</td><td className="num money" style={{ fontWeight: opts.strong ? 700 : undefined }}>{opts.minus ? "−" : ""}{fmtMoney(v)}</td></tr>
  );
  return (
    <>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}><span className="meta">Mes</span><input className="input" type="month" style={{ maxWidth: 170 }} value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} /></div>
      {t === null ? <Loading /> : (
        <div className="grid-2">
          <Card title="IVA del mes (en colones)" flush>
            <table className="table"><tbody>
              {row("IVA débito (ventas)", t.iva.debito_crc, { strong: true })}
              {row("Crédito de facturas electrónicas aceptadas", t.iva.credito_xml_crc, { minus: true })}
              {Number(t.iva.credito_por_confirmar_crc) !== 0 && row("de ese, por confirmar: sin respuesta de Hacienda al proveedor", t.iva.credito_por_confirmar_crc, { muted: true })}
              {row("Crédito de gastos sin factura electrónica vinculada", t.iva.credito_manual_crc, { minus: true, warn: Number(t.iva.credito_manual_crc) !== 0 })}
              {Number(t.iva.credito_sin_respaldo_crc) !== 0 && row("Excluido: rechazados por Hacienda al proveedor (no entra)", t.iva.credito_sin_respaldo_crc, { muted: true })}
              {row("Compras con proporcionalidad (IVA)", t.iva.proporcional_crc, { muted: true })}
              {row(`Crédito por prorrata${t.iva.prorrata_pct !== null ? ` (${Number(t.iva.prorrata_pct)} %)` : ""}`, t.iva.credito_prorrata_crc, { minus: true })}
              {row("IVA a pagar · borrador D-104", t.iva.a_pagar_crc, { strong: true })}
            </tbody></table>
            {Number(t.iva.credito_manual_crc) !== 0 && <p className="muted" style={{ padding: "8px 18px", fontSize: 12, margin: 0 }}>⚠ Gastos sin factura electrónica vinculada: sin comprobante aceptado no hay crédito (Res. 0027-2024 art. 11). Se suman al «a pagar» para que decidás.</p>}
            {t.credito_por_confirmar.length > 0 && (
              <details style={{ padding: "8px 18px" }}><summary className="meta" style={{ cursor: "pointer" }}>Comprobantes sin respuesta de Hacienda ({t.credito_por_confirmar.length}): pedila al proveedor</summary>
                <ul style={{ fontSize: 13 }}>{t.credito_por_confirmar.map((c) => <li key={c.id}><Link to={`/contabilidad/facturas/${c.id}`}>{c.issuer_name || c.clave.slice(-10)}</Link> · {fmtMoney(c.credit_crc)}</li>)}</ul>
              </details>
            )}
            {allows("accounting.editar") && (
              <div style={{ display: "flex", gap: 8, alignItems: "center", padding: "10px 18px", borderTop: "1px solid var(--hair)" }}>
                <span className="meta">Prorrata del periodo %</span><input className="input input--mono" style={{ maxWidth: 100, height: 32 }} inputMode="decimal" value={pct} onChange={(e) => setPct(e.target.value)} placeholder="vacío" /><button className="btn btn--soft btn--sm" onClick={savePct}>Guardar</button>
              </div>
            )}
          </Card>
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <Card title="Por responder" extra={<span className="meta">{t.recepciones.por_revisar} por revisar · {t.recepciones.vencidas} vencidos</span>}>
              {t.recepciones.vencen_pronto.length === 0 ? <p className="muted" style={{ margin: 0 }}>Nada vence en los próximos 2 días hábiles.</p> : (
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>{t.recepciones.vencen_pronto.map((r) => <li key={r.id}><Link to={`/contabilidad/facturas/${r.id}`}>{r.issuer_name || "—"}</Link> · {fmtMoney(r.total, r.currency)} · {fmtDate(r.deadline)} · <b style={{ color: "var(--bad)" }}>{plazo(r.business_days_left)}</b></li>)}</ul>
              )}
              <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>Del mes: {t.recepciones.recibidas} recibidos · {t.recepciones.respondidas} respondidos · {fmtMoney(t.recepciones.total_crc)}</p>
            </Card>
            <Card title="Rechazos y errores">
              {t.mensajes.rechazados.length === 0 && !t.mensajes.errores && !t.emision.rechazadas && !t.mensajes.por_enviar ? <Empty title="Sin rechazos" /> : (
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                  {t.mensajes.rechazados.map((r) => <li key={r.id}>Mensaje rechazado: <Link to={`/contabilidad/facturas/${r.id}`}>{r.issuer_name || "—"}</Link>{r.detail ? ` · ${r.detail}` : ""}</li>)}
                  {t.mensajes.errores > 0 && <li>{t.mensajes.errores} mensaje(s) con error</li>}
                  {t.mensajes.por_enviar > 0 && <li>{t.mensajes.por_enviar} mensaje(s) por enviar</li>}
                  {t.emision.rechazadas > 0 && <li>{t.emision.rechazadas} factura(s) emitida(s) rechazada(s) por Hacienda</li>}
                </ul>
              )}
              <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>Emisión directa: {t.emision.aceptadas} aceptadas · {t.emision.pendientes} pendientes</p>
            </Card>
            <Card title="Conexión con Hacienda">
              <dl className="acc-kv">
                <dt>Modo</dt><dd><b>{MODE_LABEL[t.hacienda.mode] || t.hacienda.mode}</b></dd>
                <dt>Ambiente</dt><dd>{t.hacienda.environment}</dd>
                <dt>Certificado</dt><dd>{t.hacienda.cert_not_after ? `vence el ${fmtDate(t.hacienda.cert_not_after.slice(0, 10))} · ${t.hacienda.cert_days_left} días` : "sin cargar"}</dd>
                <dt>Buzón</dt><dd>{t.hacienda.imap_last_run_at ? `revisado ${parseTs(t.hacienda.imap_last_run_at).toLocaleString("es-CR")}` : "sin revisar"}</dd>
              </dl>
              {allows("settings.configurar") && <Link className="btn btn--ghost btn--sm" style={{ marginTop: 10 }} to="/ajustes?tab=hacienda">Ajustes → Hacienda</Link>}
            </Card>
            <Card title="Reportes del mes">
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {REPORTS.map(([k, l]) => <div key={k} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}><Link to={`/reportes?r=${k}&from=${from}&to=${to}`} style={{ flex: 1 }}>{l}</Link><AuthLink path={`/reports/${k}?from=${from}&to=${to}&format=xlsx`} download={`${k}-${month}.xlsx`}>Excel</AuthLink></div>)}
              </div>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
