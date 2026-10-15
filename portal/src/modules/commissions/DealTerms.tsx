/* Acuerdo economico de un aliado en un negocio (13/10, 26:36-27:48): lo que recibe por su trabajo (sus precios
   aprobados), su comision en este proyecto y los bonos por completar y por terminar antes. Lo usan Proyectos y
   Oportunidades. Ver: commissions.ver_todo + costos; guardar: commissions.configurar. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, Modal } from "../../ui/components";
import { TRIGGER, errText, type Deal } from "./types";

type Form = {
  own: boolean; base: string; mode: "percent" | "fixed"; percent: string; fixed: string; currency: string; trigger: string;
  fin: boolean; finMode: "fixed" | "percent"; finAmount: string; finPercent: string;
  early: boolean; earlyAmount: string; earlyDate: string; earlyDays: string;
};

function toForm(d: Deal): Form {
  const c = d.commission, b = d.bonus_complete, e = d.bonus_early;
  return {
    own: !!c, base: c?.base || "venta", mode: c?.fixed_amount && Number(c.fixed_amount) > 0 ? "fixed" : "percent", percent: c?.percent ?? "", fixed: c?.fixed_amount ?? "",
    currency: c?.currency || "CRC", trigger: c?.trigger || "proyecto_completado",
    fin: !!b, finMode: b?.percent && Number(b.percent) > 0 ? "percent" : "fixed", finAmount: b?.amount ?? "", finPercent: b?.percent ?? "",
    early: !!e, earlyAmount: e?.amount ?? "", earlyDate: e?.committed_date || "", earlyDays: String(e?.early_days ?? 1),
  };
}

export default function DealTerms({ participationId, name, onClose, onSaved }: { participationId: number; name: string; onClose: () => void; onSaved?: () => void }) {
  const { toast, allows } = useSession();
  const configura = allows("commissions.configurar");
  const [d, setD] = useState<Deal | null>(null);
  const [f, setF] = useState<Form | null>(null);
  const [busy, setBusy] = useState(false);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { api<Deal>(`/commissions/deals/${participationId}`).then((x) => { setD(x); setF(toForm(x)); }).catch((e) => { toast(errText(e), "bad"); onClose(); }); }, [participationId]);
  const set = (p: Partial<Form>) => setF((x) => (x ? { ...x, ...p } : x));
  const save = async () => {
    if (!f) return;
    const body = {
      commission: f.own ? { base: f.base, percent: f.mode === "percent" ? Number(f.percent || 0) : null, fixed_amount: f.mode === "fixed" ? Number(f.fixed || 0) : null, currency: f.currency, trigger: f.trigger } : null,
      bonus_complete: f.fin ? { amount: f.finMode === "fixed" ? Number(f.finAmount || 0) : null, percent: f.finMode === "percent" ? Number(f.finPercent || 0) : null, currency: "CRC" } : null,
      bonus_early: f.early ? { amount: Number(f.earlyAmount || 0), currency: "CRC", committed_date: f.earlyDate, early_days: Number(f.earlyDays || 1) } : null,
    };
    setBusy(true);
    try { const x = await api<Deal>(`/commissions/deals/${participationId}`, { method: "PUT", json: body }); setD(x); setF(toForm(x)); toast("Acuerdo guardado"); onSaved?.(); }
    catch (e) { toast(errText(e), "bad"); } finally { setBusy(false); }
  };
  const p = d?.projection;
  const ro = !configura;
  return (
    <Modal wide title={`Acuerdo económico · ${name}`} onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cerrar</button>{configura && <button className="btn btn--crimson" disabled={busy || !f || (f.early && !f.earlyDate)} onClick={save}>Guardar acuerdo</button>}</>}>
      {!d || !f ? <div style={{ padding: 30, textAlign: "center" }}><span className="spinner" /></div> : (
        <>
          <b>Lo que recibe por su trabajo</b>
          {d.pago_acordado.length === 0 ? <p className="muted" style={{ margin: "4px 0 12px" }}>Aprobá el precio del aliado en Solicitudes de costo (en la oportunidad).</p> : (
            <ul style={{ margin: "4px 0 12px", paddingLeft: 18 }}>{d.pago_acordado.map((r) => <li key={r.id}>{r.what}: {fmtMoney(r.amount, r.currency)}</li>)}</ul>
          )}
          {d.participation.opportunity_id && allows("crm_pipeline.ver") && (
            <p style={{ margin: "-6px 0 12px" }}><Link className="row-link" to={`/oportunidades?id=${d.participation.opportunity_id}`} onClick={onClose}>Gestionar en la oportunidad</Link></p>
          )}
          <b>Comisión en este proyecto</b>
          <label className="com-check"><input type="radio" disabled={ro} checked={!f.own} onChange={() => set({ own: false })} />Usar la regla general{d.general_rule ? ` (${d.general_rule.summary})` : " (no hay: no comisiona)"}</label>
          <label className="com-check"><input type="radio" disabled={ro} checked={f.own} onChange={() => set({ own: true })} />Acuerdo propio</label>
          {f.own && (
            <div className="grid-2">
              <Field label="Cuánto">
                <div style={{ display: "flex", gap: 6 }}>
                  <select className="select" style={{ width: 130 }} disabled={ro} value={f.mode} onChange={(e) => set({ mode: e.target.value as Form["mode"] })}><option value="percent">Porcentaje</option><option value="fixed">Monto fijo</option></select>
                  {f.mode === "percent" ? <input className="input input--mono" disabled={ro} inputMode="decimal" placeholder="%" value={f.percent} onChange={(e) => set({ percent: e.target.value })} /> : <input className="input input--mono" disabled={ro} inputMode="decimal" value={f.fixed} onChange={(e) => set({ fixed: e.target.value })} />}
                </div>
              </Field>
              <Field label="Sobre qué"><select className="select" disabled={ro} value={f.base} onChange={(e) => set({ base: e.target.value })}><option value="venta">La venta sin IVA</option><option value="margen">La utilidad</option></select></Field>
              <Field label="Cuándo se gana"><select className="select" disabled={ro} value={f.trigger} onChange={(e) => set({ trigger: e.target.value })}>{["cobro", "cotizacion_aceptada", "proyecto_completado"].map((k) => <option key={k} value={k}>{TRIGGER[k]}</option>)}</select></Field>
            </div>
          )}
          <label className="com-check" style={{ marginTop: 10 }}><input type="checkbox" disabled={ro} checked={f.fin} onChange={(e) => set({ fin: e.target.checked })} /><b>Bono por completar el proyecto</b></label>
          {f.fin && (
            <div style={{ display: "flex", gap: 6, maxWidth: 360 }}>
              <select className="select" style={{ width: 130 }} disabled={ro} value={f.finMode} onChange={(e) => set({ finMode: e.target.value as Form["finMode"] })}><option value="fixed">Monto (₡)</option><option value="percent">% de la venta</option></select>
              {f.finMode === "fixed" ? <input className="input input--mono" disabled={ro} inputMode="decimal" value={f.finAmount} onChange={(e) => set({ finAmount: e.target.value })} /> : <input className="input input--mono" disabled={ro} inputMode="decimal" value={f.finPercent} onChange={(e) => set({ finPercent: e.target.value })} />}
            </div>
          )}
          <label className="com-check" style={{ marginTop: 10 }}><input type="checkbox" disabled={ro} checked={f.early} onChange={(e) => set({ early: e.target.checked })} /><b>Bono por terminar antes</b></label>
          {f.early && (
            <div className="grid-2">
              <Field label="Monto (₡)"><input className="input input--mono" disabled={ro} inputMode="decimal" value={f.earlyAmount} onChange={(e) => set({ earlyAmount: e.target.value })} /></Field>
              <Field label="Fecha comprometida"><input className="input" type="date" disabled={ro} value={f.earlyDate} onChange={(e) => set({ earlyDate: e.target.value })} /></Field>
              <Field label="Días de anticipación" hint="Mínimo para ganar el bono (1 por defecto)."><input className="input input--mono" disabled={ro} inputMode="numeric" value={f.earlyDays} onChange={(e) => set({ earlyDays: e.target.value })} /></Field>
            </div>
          )}
          {p && (
            <div className="com-example">
              Recibiría {fmtMoney(p.pago_acordado)} de pago acordado + {fmtMoney(p.comision)} de comisión{Number(p.bonos) ? ` + ${fmtMoney(p.bonos)} de bono` : ""} = <b>{fmtMoney(p.total)}</b>
              {Number(p.bono_anticipo) > 0 && d.bonus_early ? `; ${fmtMoney(p.bono_anticipo)} más si termina antes del ${fmtDate(d.bonus_early.committed_date)}` : ""}. Montos sin IVA (proyección con lo guardado).
            </div>
          )}
          {d.movimientos.length > 0 && <p className="com-note">{d.movimientos.length} movimiento(s) ya devengados en este negocio: cambiar el acuerdo no los recalcula.</p>}
        </>
      )}
    </Modal>
  );
}
