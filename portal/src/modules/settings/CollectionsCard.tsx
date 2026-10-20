/* Cobranza (plan 1.3): la secuencia de recordatorios de cobro (D-3, D0, D+3, D+10) con PDF y enlace de pago.
   Sin fecha de corte de cartera no sale nada: la cartera vieja pudo pagarse por fuera. Antes de encender los
   recordatorios, la vista previa (corrida en seco) muestra quién recibiría qué ese día, sin mandar ni guardar nada. */
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, Modal } from "../../ui/components";

export type CollectionsCfg = {
  notify_due: boolean;
  collections_start_date?: string | null;
  collections_stages?: number[];
  collections_ally_stages?: number[];
};

type Row = {
  invoice_id: number; number: string; customer: string | null; email: string | null; stage: string; stage_date: string; due_date: string;
  days: number; balance: string; currency: string; mensualidad: boolean; subject: string; reason?: string;
};
type Preview = {
  fecha: string; notify_due: boolean; start_date: string | null; stages: string[]; ally_stages: string[]; mail_configured: boolean; warnings: string[];
  items: Row[]; skipped: Row[]; ejemplo: { subject: string; html: string } | null;
  cartera: { total: string; vencida: string; n: number; n_vencidas: number; currency: string };
  baseline: { date: string; overdue: string; currency: string } | null;
};

const asText = (v?: number[]) => (v && v.length ? v.join(", ") : "");
const parse = (s: string) => s.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean).map(Number);
const today = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Costa_Rica" });

export default function CollectionsCard({ cfg, save }: { cfg: CollectionsCfg; save: (patch: Partial<CollectionsCfg>) => void }) {
  const { allows, toast } = useSession();
  const [pv, setPv] = useState<Preview | null>(null);
  const [fecha, setFecha] = useState(today());
  const [ejemplo, setEjemplo] = useState(false);
  const [busy, setBusy] = useState(false);
  const canPreview = allows("sales.ver_todo");

  const load = async (f = fecha) => {
    setBusy(true);
    try { setPv(await api<Preview>(`/collections/preview?fecha=${f}`)); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo cargar la vista previa", "bad"); }
    finally { setBusy(false); }
  };
  const baseline = async () => {
    try { await api("/collections/baseline", { method: "POST" }); toast("Línea base registrada"); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo", "bad"); }
  };
  const stages = (key: "collections_stages" | "collections_ally_stages", text: string) => {
    const v = parse(text);
    if (!v.length || v.some((x) => Number.isNaN(x))) { toast("Escribí días separados por coma, por ejemplo -3, 0, 3, 10", "bad"); return; }
    save({ [key]: v });
  };

  return (
    <Card title="Cobranza">
      <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 13 }}>
        <label style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
          <input type="checkbox" checked={cfg.notify_due} onChange={(e) => save({ notify_due: e.target.checked })} style={{ marginTop: 3 }} />
          <span><b>Recordatorios de cobro al cliente</b><br /><span className="muted">Solo facturas que ya le llegaron al cliente, con saldo. Cada recordatorio lleva el PDF y el enlace de pago. Antes de encenderlo, revisá la vista previa.</span></span>
        </label>
        <Field label="Fecha de corte de cartera" hint="Solo se recuerdan facturas emitidas desde esta fecha. Sin fecha no sale ningún recordatorio.">
          <input className="input" type="date" value={cfg.collections_start_date || ""} onChange={(e) => save({ collections_start_date: e.target.value || null })} style={{ maxWidth: 200 }} />
        </Field>
        <div className="grid-2">
          <Field label="Etapas (días respecto del vencimiento)" hint="Negativo = antes de vencer. Ej.: -3, 0, 3, 10">
            <input className="input input--mono" key={asText(cfg.collections_stages)} defaultValue={asText(cfg.collections_stages)} onBlur={(e) => e.target.value !== asText(cfg.collections_stages) && stages("collections_stages", e.target.value)} />
          </Field>
          <Field label="Etapas de mensualidades de aliados" hint="Aviso de atraso a D+5 por defecto">
            <input className="input input--mono" key={asText(cfg.collections_ally_stages)} defaultValue={asText(cfg.collections_ally_stages)} onBlur={(e) => e.target.value !== asText(cfg.collections_ally_stages) && stages("collections_ally_stages", e.target.value)} />
          </Field>
        </div>
        {canPreview && <button className="btn btn--soft btn--sm" style={{ alignSelf: "flex-start" }} onClick={() => load()}>Vista previa (corrida en seco)</button>}
      </div>

      {pv && (
        <Modal title="Vista previa de recordatorios" wide onClose={() => setPv(null)} foot={<button className="btn btn--ghost" onClick={() => setPv(null)}>Cerrar</button>}>
          <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
            <Field label="Fecha de la corrida"><input className="input" type="date" value={fecha} onChange={(e) => { setFecha(e.target.value); if (e.target.value) load(e.target.value); }} /></Field>
            <span className="muted" style={{ fontSize: 12.5, paddingBottom: 10 }}>Etapas: {pv.stages.join(" · ")} · aliados: {pv.ally_stages.join(" · ")}{busy ? " · cargando…" : ""}</span>
          </div>
          {pv.warnings.map((w) => <p key={w} className="badge badge--warn" style={{ display: "block", whiteSpace: "normal", padding: "8px 12px" }}>{w}</p>)}
          <p className="muted" style={{ fontSize: 13 }}>
            Por cobrar hoy: <b>{fmtMoney(pv.cartera.total, pv.cartera.currency)}</b> en {pv.cartera.n} factura(s) · vencida <b>{fmtMoney(pv.cartera.vencida, pv.cartera.currency)}</b> en {pv.cartera.n_vencidas}.{" "}
            {pv.baseline ? <>Línea base (día 0, {fmtDate(pv.baseline.date)}): {fmtMoney(pv.baseline.overdue, pv.baseline.currency)} vencidos.</> : "Sin línea base del día 0."}{" "}
            {allows("settings.configurar") && <button className="btn btn--ghost btn--sm" onClick={baseline}>{pv.baseline ? "Volver a registrar la línea base hoy" : "Registrar la línea base hoy"}</button>}
          </p>
          <div className="meta">Recibirían un recordatorio ({pv.items.length})</div>
          {pv.items.length === 0 ? <Empty title="Nadie recibiría un recordatorio ese día" /> : (
            <table className="table"><thead><tr><th>Factura</th><th>Cliente</th><th>Correo</th><th>Etapa</th><th>Vence</th><th className="num">Saldo</th></tr></thead>
              <tbody>{pv.items.map((r) => <tr key={r.invoice_id}><td><Link className="row-link mono" to={`/facturas/${r.invoice_id}`}>{r.number}</Link>{r.mensualidad && <div className="meta">Mensualidad</div>}</td><td>{r.customer || "—"}</td><td className="muted">{r.email}</td><td><span className="badge badge--info">{r.stage}</span></td><td className="muted">{fmtDate(r.due_date)}</td><td className="num money">{fmtMoney(r.balance, r.currency)}</td></tr>)}</tbody></table>
          )}
          {pv.skipped.length > 0 && <>
            <div className="meta">Les tocaría pero no se envían ({pv.skipped.length})</div>
            <table className="table"><thead><tr><th>Factura</th><th>Cliente</th><th>Etapa</th><th>Motivo</th><th className="num">Saldo</th></tr></thead>
              <tbody>{pv.skipped.map((r) => <tr key={r.invoice_id}><td><Link className="row-link mono" to={`/facturas/${r.invoice_id}`}>{r.number}</Link></td><td>{r.customer || "—"}</td><td>{r.stage}</td><td className="muted">{r.reason}</td><td className="num money">{fmtMoney(r.balance, r.currency)}</td></tr>)}</tbody></table>
          </>}
          {pv.ejemplo && <button className="btn btn--ghost btn--sm" style={{ alignSelf: "flex-start" }} onClick={() => setEjemplo(!ejemplo)}>{ejemplo ? "Ocultar el correo de ejemplo" : "Ver el correo de ejemplo"}</button>}
          {ejemplo && pv.ejemplo && <>
            <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Asunto: <b>{pv.ejemplo.subject}</b></p>
            <iframe title="Correo de ejemplo" sandbox="" srcDoc={pv.ejemplo.html} style={{ width: "100%", height: 320, border: "1px solid var(--line)", borderRadius: 10, background: "#fff" }} />
          </>}
        </Modal>
      )}
    </Card>
  );
}
