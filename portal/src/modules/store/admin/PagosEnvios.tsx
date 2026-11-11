/* Mi Tienda -> Pagos y envios (O4-08): todo lo que decide como se paga y como llega el pedido, en una seccion y un
   solo guardado (O4-02), con tres fuentes: /store (tarifas y datos del comercio), /store/checkout (formas de pago,
   envio gratis, retiro y avisos) y /store/inventory/settings (reglas de inventario).
   - Tarifas en tabla: nombre, zona elegida de una lista, monto base (sin IVA) con el monto con IVA al lado, por kilo,
     recargo y activa. Al lado, «Así lo ve su cliente en el checkout».
   - Formas de pago con su estado y titular (el titular distinto de la razon social se avisa).
   - Reglas de inventario con el efecto en palabras («hoy N productos salen como por confirmar»).
   Las contradicciones que marca el Resumen (retiro sin direccion, titular, formas de pago) se corrigen aqui. */
import { useEffect, useState } from "react";
import { api } from "../../../lib/api";
import { formatCRCInput, parseCRC } from "../../../lib/parseCRC";
import { useSession } from "../../../app/session";
import { registerRoles, roleMeta } from "../../../app/roles";
import { Card, Field, I, Icon, Loading } from "../../../ui/components";
import { checkRate, freeEverywhere, suspiciousAmount, type Rate } from "../rates";
import { SaveBar } from "./SaveBar";
import type { Health } from "./StoreResumen";
import { useDraft } from "./useDraft";

type Business = { legal_name?: string; legal_id?: string; address?: string; phone?: string; email?: string; hours?: string };
type StoreSide = { shipping_rates: Rate[]; business: Business };
type Methods = {
  sinpe: { active: boolean; phone?: string | null; holder?: string | null; note?: string | null };
  transferencia: { active: boolean; bank?: string | null; iban?: string | null; holder?: string | null; holder_id?: string | null; note?: string | null };
  tarjeta: { active: boolean; installments?: boolean; max_installments?: number };
  efectivo: { active: boolean; note?: string | null; pickup_only?: boolean };
};
type Ck = {
  methods: Methods; pickup: { address?: string | null; hours?: string | null }; notify_roles: string[]; free_shipping_from: number | string;
  shipping_cabys: string; auto_invoice: boolean; verify_email: boolean; delivery_notice: boolean; seller_choice: boolean; carriers: string[];
};
type Inv = { supplier_max_age_hours: number; backorder_enabled: boolean; backorder_lead: string };
type CkResp = { checkout: Ck; onvo: { configured: boolean; simulation: boolean; mode: string | null }; role_choices?: string[]; role_labels?: Record<string, string> };

const ZONAS: [string, string][] = [["retiro", "Retiro en sitio"], ["gam", "Solo GAM"], ["fuera_gam", "Fuera de GAM"], ["", "Todo el país"]];
const AREA: Record<string, string> = { gam: "en la GAM", fuera_gam: "fuera de la GAM", "": "a todo el país" };
const LABELS: Record<string, string> = { shipping_rates: "Tarifas de envío", business: "Datos del comercio", checkout: "Pagos y entrega", supplier_max_age_hours: "Vigencia del dato del proveedor", backorder_enabled: "Bajo pedido", backorder_lead: "Plazo bajo pedido" };
const BIZ: [keyof Business, string, string][] = [
  ["legal_name", "Razón social", "Comercio de Ejemplo S.A."], ["legal_id", "Cédula jurídica", "3-101-000000"], ["phone", "Teléfono", "+506 0000-0000"],
  ["email", "Correo", "ventas@ejemplo.com"], ["hours", "Horario", "lunes a viernes de 9:00 a. m. a 6:00 p. m."], ["address", "Dirección (opcional)", "Provincia, cantón y señas"],
];
const norm = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "");
const conIva = (net: number) => Math.round(net * 1.13 * 100) / 100;
const zoneOf = (r: Rate) => (r.zone === "retiro" || r.zone === "gam" || r.zone === "fuera_gam" ? r.zone : /retiro|recoger/i.test(r.name) ? "retiro" : /fuera/i.test(r.name) ? "fuera_gam" : /gam/i.test(r.name) ? "gam" : "");

export default function PagosEnvios() {
  const { toast } = useSession();
  const [extra, setExtra] = useState<Omit<CkResp, "checkout"> | null>(null);
  const [salud, setSalud] = useState<Health | null>(null);
  const st = useDraft<StoreSide>({ key: "pagos:store", url: "/store", read: (r) => ({ shipping_rates: (r.shipping_rates as Rate[]) || [], business: (r.business as Business) || {} }) });
  const ck = useDraft<{ checkout: Ck }>({
    key: "pagos:checkout",
    url: "/store/checkout",
    read: (r) => { const x = r as unknown as CkResp; registerRoles({ labels: x.role_labels }); setExtra({ onvo: x.onvo, role_choices: x.role_choices, role_labels: x.role_labels }); return { checkout: x.checkout }; },
    // el PUT pide el checkout completo y solo con las llaves que conoce
    body: (x, _c, version) => {
      const c = x.checkout, m = c.methods;
      return {
        methods: {
          sinpe: { active: m.sinpe.active, phone: m.sinpe.phone || null, holder: m.sinpe.holder || null, note: m.sinpe.note || null },
          transferencia: { active: m.transferencia.active, bank: m.transferencia.bank || null, iban: m.transferencia.iban || null, holder: m.transferencia.holder || null, holder_id: m.transferencia.holder_id || null, note: m.transferencia.note || null },
          tarjeta: { active: m.tarjeta.active, installments: !!m.tarjeta.installments, max_installments: Number(m.tarjeta.max_installments || 0) },
          efectivo: { active: m.efectivo.active, note: m.efectivo.note || null, pickup_only: m.efectivo.pickup_only !== false },
        },
        pickup: { address: c.pickup?.address || null, hours: c.pickup?.hours || null },
        notify_roles: c.notify_roles, free_shipping_from: String(Number(c.free_shipping_from) || 0), shipping_cabys: c.shipping_cabys || "6803000000000",
        auto_invoice: c.auto_invoice, verify_email: c.verify_email !== false, delivery_notice: c.delivery_notice !== false, seller_choice: !!c.seller_choice,
        carriers: (c.carriers || []).map((s) => s.trim()).filter(Boolean), version,
      };
    },
  });
  const inv = useDraft<Inv>({ key: "pagos:inventario", url: "/store/inventory/settings", read: (r) => ({ supplier_max_age_hours: Number(r.supplier_max_age_hours ?? 48), backorder_enabled: !!r.backorder_enabled, backorder_lead: String(r.backorder_lead ?? "") }) });
  useEffect(() => { api<Health>("/store/health").then(setSalud).catch(() => setSalud(null)); }, []);
  const err = st.error || ck.error || inv.error;
  if (err) return <Card title="Pagos y envíos"><p className="mt-lead">{err}</p></Card>;
  if (!st.draft || !ck.draft || !inv.draft) return <Loading />;
  const rates = st.draft.shipping_rates;
  const biz = st.draft.business;
  const c = ck.draft.checkout;
  const m = c.methods;
  const setRates = (r: Rate[]) => st.set({ shipping_rates: r });
  const setC = (patch: Partial<Ck>) => ck.set({ checkout: { ...c, ...patch } });
  const setM = <K extends keyof Methods>(k: K, patch: Partial<Methods[K]>) => setC({ methods: { ...m, [k]: { ...m[k], ...patch } } });
  const gratis = Number(c.free_shipping_from) || 0;
  const activos = rates.filter((r) => r.active);
  const legal = (biz.legal_name || "").trim();
  const titularMal = (h?: string | null) => !!h && !!legal && norm(h) !== norm(legal);
  const validar = () => {
    for (const [i, r] of rates.entries()) {
      const chk = checkRate({ name: r.name, amount: String(r.amount), per_kg: String(r.per_kg ?? 0), overhead_pct: String(r.overhead_pct ?? 0), active: r.active }, rates.filter((_, k) => k !== i));
      if (!chk.ok) return `Tarifa ${i + 1}: ${Object.values(chk.errors).join(" · ")}`;
    }
    if (rates.some((r) => freeEverywhere(r)) && !confirm("Una tarifa activa queda gratis para todo el país. ¿Guardar así?")) return "Revise la tarifa gratis antes de guardar.";
    if (m.sinpe.active && (m.sinpe.phone || "").replace(/\D/g, "").length < 8) return "El número de SINPE Móvil debe tener 8 dígitos.";
    if (!/^\d{13}$/.test(c.shipping_cabys || "")) return "El CABYS del envío lleva 13 dígitos.";
    return null;
  };

  return (
    <>
      <div className="pe-grid">
        <Card title="Tarifas de envío" extra={<button className="btn btn--ghost btn--sm" onClick={() => setRates([...rates, { name: "", amount: 0, active: true, per_kg: 0, overhead_pct: 0, zone: "gam" }])} disabled={rates.length >= 50}><Icon d={I.plus} />Tarifa</button>}>
          <div className="mt-stack">
            <table className="table table--stack pe-rates" aria-label="Tarifas de envío">
              <thead><tr><th>Nombre</th><th>Zona</th><th className="num">Base sin IVA</th><th className="num">Por kilo</th><th className="num">Recargo %</th><th>Activa</th><th /></tr></thead>
              <tbody>
                {rates.map((r, i) => (
                  <RateRow key={r.id || `n${i}`} r={r} onChange={(p) => setRates(rates.map((x, k) => (k === i ? { ...x, ...p } : x)))} onRemove={() => setRates(rates.filter((_, k) => k !== i))} />
                ))}
              </tbody>
            </table>
            <p className="meta" style={{ textTransform: "none", margin: 0 }}>Tarifa fija, o por peso estilo Correos de Costa Rica: (base + ₡/kg × peso del pedido, mínimo 1 kg) + recargo. Se cobra con IVA 13 %. Escriba los montos en colones, por ejemplo 4.700.</p>
          </div>
        </Card>
        <Card title="Así lo ve su cliente en el checkout">
          <ul className="pe-preview">
            {activos.length === 0 && <li>No hay tarifas activas: el checkout no ofrece envío.</li>}
            {activos.map((r, i) => {
              const z = zoneOf(r);
              return <li key={i}><b>{r.name || "Sin nombre"}</b> · {z === "retiro" ? (c.pickup?.address ? `Retiro en ${c.pickup.address}` : "Retiro (falta la dirección)") : `₡${formatCRCInput(conIva(Number(r.amount) || 0))} ${AREA[z]}${Number(r.per_kg) ? " más el peso" : ""}`}</li>;
            })}
            {gratis > 0 && <li>Envío gratis desde ₡{formatCRCInput(gratis)} (con IVA).</li>}
            <li>Paga con: {[m.tarjeta.active && "tarjeta", m.sinpe.active && "SINPE Móvil", m.transferencia.active && "transferencia", m.efectivo.active && (m.efectivo.pickup_only !== false ? "efectivo al retirar" : "efectivo")].filter(Boolean).join(", ") || "ninguna forma de pago activa"}.</li>
          </ul>
        </Card>
      </div>

      <Card title="Envío gratis y retiro en sitio">
        <div className="grid-2">
          <Field label="Envío gratis desde (₡, con IVA)" hint="0 = nunca."><MoneyInput value={gratis} onChange={(v) => setC({ free_shipping_from: v })} label="Envío gratis desde" /></Field>
          <Field label="Dirección de retiro" hint="Sin dirección, la tarifa de retiro no se ofrece bien."><input className="input" value={c.pickup?.address || ""} onChange={(e) => setC({ pickup: { ...c.pickup, address: e.target.value } })} /></Field>
          <Field label="Horario de retiro"><input className="input" value={c.pickup?.hours || ""} onChange={(e) => setC({ pickup: { ...c.pickup, hours: e.target.value } })} placeholder="L-V 8 a. m. a 5 p. m." /></Field>
        </div>
      </Card>

      <Card title="Formas de pago">
        <div className="mt-stack">
          <div className="mt-box">
            <label className="pw-check"><input type="checkbox" checked={m.sinpe.active} onChange={(e) => setM("sinpe", { active: e.target.checked })} /><b>SINPE Móvil</b> <span className={`badge ${m.sinpe.active ? "badge--ok" : "badge--muted"}`}>{m.sinpe.active ? "Activo" : "Apagado"}</span></label>
            <div className="grid-2">
              <Field label="Número SINPE"><input className="input" value={m.sinpe.phone || ""} onChange={(e) => setM("sinpe", { phone: e.target.value })} placeholder="8888-8888" /></Field>
              <Field label="Titular del SINPE"><input className="input" value={m.sinpe.holder || ""} onChange={(e) => setM("sinpe", { holder: e.target.value })} /></Field>
            </div>
            {m.sinpe.active && titularMal(m.sinpe.holder) && <p className="mt-warn">El titular del SINPE no coincide con la razón social; sus clientes lo van a notar.</p>}
          </div>
          <div className="mt-box">
            <label className="pw-check"><input type="checkbox" checked={m.transferencia.active} onChange={(e) => setM("transferencia", { active: e.target.checked })} /><b>Transferencia</b> <span className={`badge ${m.transferencia.active ? "badge--ok" : "badge--muted"}`}>{m.transferencia.active ? "Activa" : "Apagada"}</span></label>
            <div className="grid-2">
              <Field label="Cuenta IBAN"><input className="input input--mono" style={{ textAlign: "left" }} value={m.transferencia.iban || ""} onChange={(e) => setM("transferencia", { iban: e.target.value })} placeholder="CR00 0000 0000 0000 0000 00" /></Field>
              <Field label="Banco"><input className="input" value={m.transferencia.bank || ""} onChange={(e) => setM("transferencia", { bank: e.target.value })} /></Field>
              <Field label="Titular de la cuenta"><input className="input" value={m.transferencia.holder || ""} onChange={(e) => setM("transferencia", { holder: e.target.value })} /></Field>
              <Field label="Cédula del titular"><input className="input" value={m.transferencia.holder_id || ""} onChange={(e) => setM("transferencia", { holder_id: e.target.value })} /></Field>
            </div>
            {m.transferencia.active && titularMal(m.transferencia.holder) && <p className="mt-warn">El titular de la cuenta no coincide con la razón social.</p>}
          </div>
          <div className="mt-box">
            <label className="pw-check"><input type="checkbox" checked={m.tarjeta.active} onChange={(e) => setM("tarjeta", { active: e.target.checked })} /><b>Tarjeta (ONVO)</b> <span className={`badge ${m.tarjeta.active ? "badge--ok" : "badge--muted"}`}>{m.tarjeta.active ? "Activa" : "Apagada"}</span></label>
            <p className="meta" style={{ textTransform: "none", margin: 0 }}>{extra?.onvo.configured ? `ONVO conectado (modo ${extra.onvo.mode === "live" ? "Producción" : "Pruebas"}).` : extra?.onvo.simulation ? "ONVO sin credenciales: en este entorno el pago con tarjeta es simulado." : "ONVO sin credenciales: la tarjeta no se ofrece hasta conectarlo (Ajustes → Pasarelas)."}</p>
          </div>
          <div className="mt-box">
            <label className="pw-check"><input type="checkbox" checked={m.efectivo.active} onChange={(e) => setM("efectivo", { active: e.target.checked })} /><b>Efectivo</b> <span className={`badge ${m.efectivo.active ? "badge--ok" : "badge--muted"}`}>{m.efectivo.active ? "Activo" : "Apagado"}</span></label>
            <label className="pw-check"><input type="checkbox" checked={m.efectivo.pickup_only !== false} onChange={(e) => setM("efectivo", { pickup_only: e.target.checked })} />Solo al retirar en sitio</label>
          </div>
          <p className="meta" style={{ textTransform: "none", margin: 0 }}>La respuesta a «¿Cómo pago?» de las preguntas frecuentes sale de estas formas de pago activas.</p>
        </div>
      </Card>

      <Card title="Reglas de inventario">
        <div className="mt-stack">
          <div className="grid-2">
            <Field label="Vigencia del dato del proveedor (horas)" hint="Más viejo que esto, el producto sale como «por confirmar» y no se puede comprar en línea."><input className="input input--mono" type="number" min={1} max={1440} value={inv.draft.supplier_max_age_hours} onChange={(e) => inv.set({ supplier_max_age_hours: Math.max(1, Math.min(1440, Number(e.target.value) || 1)) })} /></Field>
            <Field label="Plazo de entrega bajo pedido" hint="Lo que ve el cliente, por ejemplo «3 a 5 días hábiles»."><input className="input" value={inv.draft.backorder_lead} maxLength={60} onChange={(e) => inv.set({ backorder_lead: e.target.value })} /></Field>
          </div>
          <label className="pw-check"><input type="checkbox" checked={inv.draft.backorder_enabled} onChange={(e) => inv.set({ backorder_enabled: e.target.checked })} />Vender bajo pedido lo que el proveedor tiene con dato vigente</label>
          {salud && <p className="mt-lead">Con {inv.server?.supplier_max_age_hours} horas de vigencia, hoy {salud.indicadores.solicitables.toLocaleString("es-CR")} de {salud.indicadores.visibles.toLocaleString("es-CR")} productos visibles salen como «por confirmar» y no se pueden comprar en línea.</p>}
        </div>
      </Card>

      <Card title="Datos del comercio">
        <div className="mt-stack">
          <p className="mt-lead">Aparecen en el pie de la tienda, en el checkout y en los correos.</p>
          <div className="grid-2">
            {BIZ.map(([k, label, ph]) => (
              <Field key={k} label={label}><input className="input" value={biz[k] || ""} placeholder={ph} maxLength={k === "address" ? 200 : 120} onChange={(e) => st.set({ business: { ...biz, [k]: e.target.value } })} /></Field>
            ))}
          </div>
        </div>
      </Card>

      <details className="card pe-more">
        <summary className="card__head"><b>Más opciones</b></summary>
        <div className="card__body mt-stack">
          <Field label="CABYS del envío" hint="13 dígitos. Por defecto 6803000000000; confírmelo con su contador."><input className="input input--mono" inputMode="numeric" maxLength={13} value={c.shipping_cabys} onChange={(e) => setC({ shipping_cabys: e.target.value.replace(/\D/g, "") })} /></Field>
          <label className="pw-check"><input type="checkbox" checked={c.auto_invoice} onChange={(e) => setC({ auto_invoice: e.target.checked })} />Emitir la factura electrónica al confirmarse el pago</label>
          <label className="pw-check"><input type="checkbox" checked={c.verify_email !== false} onChange={(e) => setC({ verify_email: e.target.checked })} />Revisar que el correo del comprador exista</label>
          <label className="pw-check"><input type="checkbox" checked={c.delivery_notice !== false} onChange={(e) => setC({ delivery_notice: e.target.checked })} />Avisar por correo de cada envío a domicilio</label>
          <label className="pw-check"><input type="checkbox" checked={!!c.seller_choice} onChange={(e) => setC({ seller_choice: e.target.checked })} />Que el cliente elija al vendedor que lo atendió</label>
          {extra?.role_choices && extra.role_choices.length > 0 && (
            <Field label="Quién recibe el aviso de cada pedido web">
              <div className="mt-row">{extra.role_choices.map((r) => <label key={r} className="pw-check"><input type="checkbox" checked={c.notify_roles.includes(r)} onChange={(e) => setC({ notify_roles: e.target.checked ? [...c.notify_roles, r] : c.notify_roles.filter((x) => x !== r) })} />{roleMeta(r).label}</label>)}</div>
            </Field>
          )}
          <Field label="Transportistas al marcar «Enviado»" hint="Uno por línea."><textarea className="input" rows={3} value={(c.carriers || []).join("\n")} onChange={(e) => setC({ carriers: e.target.value.split("\n") })} /></Field>
        </div>
      </details>
      <SaveBar drafts={[st, ck, inv]} labels={LABELS} validate={validar} onSaved={() => api<Health>("/store/health").then(setSalud).catch(() => toast("No se pudo releer el resumen", "bad"))} />
    </>
  );
}

function MoneyInput({ value, onChange, label }: { value: number; onChange: (v: number) => void; label: string }) {
  const [t, setT] = useState(formatCRCInput(value));
  const [bad, setBad] = useState(false);
  useEffect(() => { const r = parseCRC(t); if (!r.ok || r.value !== value) setT(formatCRCInput(value)); }, [value]); // eslint-disable-line react-hooks/exhaustive-deps
  return <input className="input input--mono" inputMode="decimal" aria-label={label} aria-invalid={bad || undefined} value={t} onChange={(e) => { setT(e.target.value); const r = parseCRC(e.target.value); setBad(!r.ok); if (r.ok) onChange(r.value); }} />;
}

function RateRow({ r, onChange, onRemove }: { r: Rate; onChange: (p: Partial<Rate>) => void; onRemove: () => void }) {
  const z = zoneOf(r);
  return (
    <tr>
      <td data-label="Nombre"><input className="input" aria-label="Nombre de la tarifa" value={r.name} maxLength={60} onChange={(e) => onChange({ name: e.target.value })} /></td>
      <td data-label="Zona"><select className="select" aria-label={`Zona de ${r.name || "la tarifa"}`} value={z} onChange={(e) => onChange({ zone: e.target.value || null })}>{ZONAS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></td>
      <td data-label="Base sin IVA" className="num"><MoneyInput value={Number(r.amount) || 0} onChange={(v) => onChange({ amount: v })} label="Base en colones" /><small className="meta" style={{ textTransform: "none" }}>con IVA: ₡{formatCRCInput(conIva(Number(r.amount) || 0))}</small>{suspiciousAmount(r) && <small className="mt-warn" style={{ display: "block" }}>¿Monto muy bajo?</small>}</td>
      <td data-label="Por kilo" className="num"><MoneyInput value={Number(r.per_kg) || 0} onChange={(v) => onChange({ per_kg: v })} label="Colones por kilo" /></td>
      <td data-label="Recargo %" className="num"><input className="input input--mono" aria-label="Recargo en porcentaje" inputMode="decimal" value={String(r.overhead_pct ?? 0)} onChange={(e) => { const v = Number(e.target.value.replace(",", ".")); if (Number.isFinite(v) && v >= 0 && v <= 100) onChange({ overhead_pct: v }); }} /></td>
      <td data-label="Activa"><input type="checkbox" aria-label={`Activa: ${r.name}`} checked={r.active} onChange={(e) => onChange({ active: e.target.checked })} /></td>
      <td><button className="x" onClick={onRemove} aria-label={`Quitar la tarifa ${r.name}`}><Icon d={I.x} size={14} /></button></td>
    </tr>
  );
}
