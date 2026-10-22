/* Acuerdo economico de un aliado en un negocio (13/10, 26:36-27:48): lo que recibe por su trabajo (sus precios
   aprobados), su comision en este proyecto y los bonos por completar y por terminar antes. Lo usan Proyectos y
   Oportunidades. Ver: commissions.ver_todo + costos; guardar: commissions.configurar.
   Reunión 2 (21/10, pedido 22: "es poca información"): cada opción trae qué significa y un ejemplo con números (con la
   venta de este proyecto si ya la tiene; si no, con una venta de ₡1 000 000). */
import { useEffect, useState, type ReactNode } from "react";
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
  /* Ejemplos (pedido 22): la venta del proyecto si ya existe; si no, una de ₡1 000 000. La utilidad, al 30 % de esa venta. */
  const conVenta = Number(p?.base_price) > 0;
  const venta = conVenta ? Number(p!.base_price) : 1000000;
  const utilidad = Math.round(venta * 0.3);
  /* la venta del proyecto llega convertida a la moneda de la proyección (hoy colones); el ejemplo inventado, en colones */
  const cur = conVenta ? p?.currency || "CRC" : "CRC";
  const money = (v: number) => fmtMoney(v, cur);
  const ventaTxt = conVenta ? `la venta de este proyecto (${money(venta)} sin IVA)` : `una venta de ${money(venta)} sin IVA`;
  const pct = (base: number, x: string) => Math.round((base * Number(x || 0)) / 100);
  return (
    <Modal wide title={`Acuerdo económico · ${name}`} onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cerrar</button>{configura && <button className="btn btn--crimson" disabled={busy || !f || (f.early && !f.earlyDate)} onClick={save}>Guardar acuerdo</button>}</>}>
      {!d || !f ? <div style={{ padding: 30, textAlign: "center" }}><span className="spinner" /></div> : (
        <>
          <b>Lo que recibe por su trabajo</b>
          <Ayuda>Es el pago por lo que el aliado hace o suministra: sus precios aprobados en las solicitudes de costo. No es comisión y se suma aparte. Ejemplo: si se le aprobó «Instalación de 8 cámaras» en ₡400 000, recibe ese monto además de cualquier comisión o bono.</Ayuda>
          {d.pago_acordado.length === 0 ? <p className="muted" style={{ margin: "4px 0 12px" }}>Aprobá el precio del aliado en Solicitudes de costo (en la oportunidad).</p> : (
            <ul style={{ margin: "4px 0 12px", paddingLeft: 18 }}>{d.pago_acordado.map((r) => <li key={r.id}>{r.what}: {fmtMoney(r.amount, r.currency)}</li>)}</ul>
          )}
          {d.participation.opportunity_id && allows("crm_pipeline.ver") && (
            <p style={{ margin: "-6px 0 12px" }}><Link className="row-link" to={`/oportunidades?id=${d.participation.opportunity_id}`} onClick={onClose}>Gestionar en la oportunidad</Link></p>
          )}
          <b>Comisión en este proyecto</b>
          <Ayuda>La comisión es lo que el aliado gana por el negocio (por traerlo o por participar), aparte de su pago por el trabajo. Se elige una de dos:</Ayuda>
          <label className="com-check"><input type="radio" disabled={ro} checked={!f.own} onChange={() => set({ own: false })} />Usar la regla general{d.general_rule ? ` (${d.general_rule.summary})` : " (no hay: no comisiona)"}</label>
          <Ayuda>
            {d.general_rule
              ? <>La misma comisión que todos los aliados, definida en Comisiones → Reglas. Si la regla cambia, este proyecto la sigue. Ejemplo: si la regla es 5 % de la venta, con {ventaTxt} el aliado gana {money(pct(venta, "5"))}.</>
              : <>Hoy no hay una regla general para aliados (se crea en Comisiones → Reglas): con esta opción el aliado no gana comisión en este proyecto, solo su pago por el trabajo y los bonos que se marquen abajo.</>}
          </Ayuda>
          <label className="com-check"><input type="radio" disabled={ro} checked={f.own} onChange={() => set({ own: true })} />Acuerdo propio</label>
          <Ayuda>Una comisión solo para este proyecto, que reemplaza la regla general. Puede ser un porcentaje de la venta sin IVA o de la utilidad, o un monto fijo. Ejemplos con {ventaTxt}: 5 % de la venta = {money(pct(venta, "5"))}; 8 % de una utilidad de {money(utilidad)} = {money(pct(utilidad, "8"))}; o un monto fijo de ₡40 000.</Ayuda>
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
          {f.own && (f.mode === "fixed" ? Number(f.fixed) > 0 : Number(f.percent) > 0) && (
            <Ayuda>
              Con lo escrito: {f.mode === "fixed" ? <>recibe {fmtMoney(Number(f.fixed), f.currency)} fijos</> : f.base === "margen" ? <>{f.percent} % de una utilidad de {money(utilidad)} = {money(pct(utilidad, f.percent))}</> : <>{f.percent} % de {money(venta)} = {money(pct(venta, f.percent))}</>}
              {f.trigger === "cobro" ? ", en partes a medida que el cliente paga (si paga la mitad, gana la mitad)." : f.trigger === "cotizacion_aceptada" ? ", cuando el cliente acepta la cotización." : ", cuando el proyecto se marca como completado."}
            </Ayuda>
          )}
          <label className="com-check" style={{ marginTop: 10 }}><input type="checkbox" disabled={ro} checked={f.fin} onChange={(e) => set({ fin: e.target.checked })} /><b>Bono por completar el proyecto</b></label>
          <Ayuda>Un extra, además de la comisión, cuando el proyecto se marca como completado. Ejemplo: ₡25 000 fijos, o 2 % de {ventaTxt} = {money(pct(venta, "2"))}.</Ayuda>
          {f.fin && (
            <div style={{ display: "flex", gap: 6, maxWidth: 360 }}>
              <select className="select" style={{ width: 130 }} disabled={ro} value={f.finMode} onChange={(e) => set({ finMode: e.target.value as Form["finMode"] })}><option value="fixed">Monto (₡)</option><option value="percent">% de la venta</option></select>
              {f.finMode === "fixed" ? <input className="input input--mono" disabled={ro} inputMode="decimal" value={f.finAmount} onChange={(e) => set({ finAmount: e.target.value })} /> : <input className="input input--mono" disabled={ro} inputMode="decimal" value={f.finPercent} onChange={(e) => set({ finPercent: e.target.value })} />}
            </div>
          )}
          <label className="com-check" style={{ marginTop: 10 }}><input type="checkbox" disabled={ro} checked={f.early} onChange={(e) => set({ early: e.target.checked })} /><b>Bono por terminar antes</b></label>
          <Ayuda>Un extra si el proyecto queda completado antes de la fecha comprometida, con al menos los días de anticipación indicados. Ejemplo: fecha comprometida 30 de noviembre, 3 días de anticipación y ₡15 000: si se completa el 27 de noviembre o antes gana los ₡15 000; si se completa el 28, no.</Ayuda>
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

/* Texto de ayuda bajo cada opción del acuerdo (pedido 22). */
function Ayuda({ children }: { children: ReactNode }) {
  return <p className="com-note" style={{ margin: "0 0 8px 24px", lineHeight: 1.45 }}>{children}</p>;
}
