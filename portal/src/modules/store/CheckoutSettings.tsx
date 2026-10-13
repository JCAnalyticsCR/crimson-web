/* Mi Tienda -> Pagos y entrega: metodos de pago del checkout (SINPE, transferencia, tarjeta ONVO, efectivo), plazo
   para pagar, retiro en sitio, envio gratis, factura automatica y la zona de cada tarifa de envio. */
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Field, I, Icon, Loading } from "../../ui/components";
import "./orders.css";

type Methods = {
  sinpe: { active: boolean; phone?: string; holder?: string; note?: string };
  transferencia: { active: boolean; bank?: string; iban?: string; holder?: string; holder_id?: string; note?: string };
  tarjeta: { active: boolean; installments?: boolean; max_installments?: number };
  efectivo: { active: boolean; note?: string };
};
type Ck = { methods: Methods; pickup: { address?: string; hours?: string }; notify_roles: string[]; free_shipping_from: number | string; auto_invoice: boolean };
type Resp = { checkout: Ck; onvo: { configured: boolean; simulation: boolean } };
export type ZoneRate = { name: string; amount: number; active: boolean; zone?: string | null; [k: string]: unknown };

const ZONES: [string, string][] = [["", "Según el nombre"], ["retiro", "Retiro en sitio"], ["gam", "Solo GAM"], ["fuera_gam", "Fuera de GAM"]];

export default function CheckoutSettings({ rates, onRates }: { rates: ZoneRate[]; onRates: (r: ZoneRate[]) => void }) {
  const { toast } = useSession();
  const [d, setD] = useState<Resp | null>(null);
  const [ck, setCk] = useState<Ck | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<Resp>("/store/checkout").then((r) => { setD(r); setCk(r.checkout); }).catch((e) => toast(e.message, "bad")); }, [toast]);
  if (!ck || !d) return <Card title="Pagos y entrega"><Loading /></Card>;
  const m = ck.methods;
  const setM = <K extends keyof Methods>(k: K, patch: Partial<Methods[K]>) => setCk({ ...ck, methods: { ...m, [k]: { ...m[k], ...patch } } });
  const save = async () => {
    setBusy(true);
    try {
      const body = {
        methods: {
          sinpe: { active: m.sinpe.active, phone: m.sinpe.phone || null, holder: m.sinpe.holder || null, note: m.sinpe.note || null },
          transferencia: { active: m.transferencia.active, bank: m.transferencia.bank || null, iban: m.transferencia.iban || null, holder: m.transferencia.holder || null, holder_id: m.transferencia.holder_id || null, note: m.transferencia.note || null },
          tarjeta: { active: m.tarjeta.active, installments: !!m.tarjeta.installments, max_installments: Number(m.tarjeta.max_installments || 0) },
          efectivo: { active: m.efectivo.active, note: m.efectivo.note || null },
        },
        pickup: { address: ck.pickup?.address || null, hours: ck.pickup?.hours || null },
        notify_roles: ck.notify_roles,
        free_shipping_from: String(Number(ck.free_shipping_from) || 0),
        auto_invoice: ck.auto_invoice,
      };
      const r = await api<Resp>("/store/checkout", { method: "PUT", json: body });
      setD(r); setCk(r.checkout); toast("Pagos y entrega guardados");
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } finally { setBusy(false); }
  };
  const on = (v: boolean) => String(v);

  return (
    <Card title="Pagos y entrega" extra={<button className="btn btn--crimson btn--sm" onClick={save} disabled={busy}><Icon d={I.check} />{busy ? "Guardando…" : "Guardar"}</button>}>
      <div className="ck-cfg">
        <div className="ck-cfg__m" data-on={on(m.sinpe.active)}>
          <label><input type="checkbox" checked={m.sinpe.active} onChange={(e) => setM("sinpe", { active: e.target.checked })} />SINPE Móvil</label>
          <div className="grid-2">
            <Field label="Número SINPE"><input className="input" value={m.sinpe.phone || ""} onChange={(e) => setM("sinpe", { phone: e.target.value })} placeholder="8888-8888" /></Field>
            <Field label="A nombre de"><input className="input" value={m.sinpe.holder || ""} onChange={(e) => setM("sinpe", { holder: e.target.value })} /></Field>
          </div>
        </div>
        <div className="ck-cfg__m" data-on={on(m.transferencia.active)}>
          <label><input type="checkbox" checked={m.transferencia.active} onChange={(e) => setM("transferencia", { active: e.target.checked })} />Transferencia bancaria</label>
          <div className="grid-2">
            <Field label="Cuenta IBAN"><input className="input input--mono" style={{ textAlign: "left" }} value={m.transferencia.iban || ""} onChange={(e) => setM("transferencia", { iban: e.target.value })} placeholder="CR00 0000 0000 0000 0000 00" /></Field>
            <Field label="Banco"><input className="input" value={m.transferencia.bank || ""} onChange={(e) => setM("transferencia", { bank: e.target.value })} /></Field>
            <Field label="Titular"><input className="input" value={m.transferencia.holder || ""} onChange={(e) => setM("transferencia", { holder: e.target.value })} /></Field>
            <Field label="Cédula del titular"><input className="input" value={m.transferencia.holder_id || ""} onChange={(e) => setM("transferencia", { holder_id: e.target.value })} /></Field>
          </div>
        </div>
        <div className="ck-cfg__m" data-on={on(m.tarjeta.active)}>
          <label><input type="checkbox" checked={m.tarjeta.active} onChange={(e) => setM("tarjeta", { active: e.target.checked })} />Tarjeta (ONVO)</label>
          <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
            {d.onvo.configured ? "ONVO conectado: el pago se confirma por webhook, nunca por el regreso del navegador."
              : d.onvo.simulation ? "ONVO sin credenciales: en este entorno el pago con tarjeta es SIMULADO (de prueba) y la tienda lo dice."
              : "ONVO sin credenciales: en producción la tarjeta no se ofrece hasta conectarlo (Ajustes → Pasarelas)."}
          </p>
          <div className="grid-2">
            <label style={{ fontSize: 13, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={!!m.tarjeta.installments} onChange={(e) => setM("tarjeta", { installments: e.target.checked })} />Ofrecer cuotas</label>
            <Field label="Máximo de cuotas" hint="Depende del contrato con ONVO (tasa cero)."><input className="input input--mono" type="number" min={0} max={36} value={m.tarjeta.max_installments || 0} onChange={(e) => setM("tarjeta", { max_installments: Number(e.target.value) })} /></Field>
          </div>
        </div>
        <div className="ck-cfg__m" data-on={on(m.efectivo.active)}>
          <label><input type="checkbox" checked={m.efectivo.active} onChange={(e) => setM("efectivo", { active: e.target.checked })} />Efectivo contra entrega o al retirar</label>
          <Field label="Texto para el cliente"><input className="input" value={m.efectivo.note || ""} onChange={(e) => setM("efectivo", { note: e.target.value })} /></Field>
        </div>
        <div className="grid-2">
          <Field label="Envío gratis desde (₡, con IVA)" hint="0 = nunca. Se convierte si la tienda vende en dólares."><input className="input input--mono" type="number" min={0} value={ck.free_shipping_from} onChange={(e) => setCk({ ...ck, free_shipping_from: e.target.value })} /></Field>
          <Field label="Dirección de retiro"><input className="input" value={ck.pickup?.address || ""} onChange={(e) => setCk({ ...ck, pickup: { ...ck.pickup, address: e.target.value } })} placeholder="Oficinas, Escazú" /></Field>
          <Field label="Horario de retiro"><input className="input" value={ck.pickup?.hours || ""} onChange={(e) => setCk({ ...ck, pickup: { ...ck.pickup, hours: e.target.value } })} placeholder="L-V 8 a. m. a 5 p. m." /></Field>
        </div>
        <label style={{ fontSize: 13, display: "flex", gap: 8, alignItems: "center" }}><input type="checkbox" checked={ck.auto_invoice} onChange={(e) => setCk({ ...ck, auto_invoice: e.target.checked })} />Emitir la factura electrónica automáticamente al confirmarse el pago (con cédula: FE; sin cédula: tiquete) y enviarla al cliente</label>
        {rates.length > 0 && (
          <div className="ck-cfg__m" data-on="true">
            <span className="meta">Zona de cada tarifa de envío</span>
            {rates.map((r, i) => (
              <div key={i} className="ck-zone"><span>{r.name}{!r.active && <span className="muted"> (inactiva)</span>}</span>
                <select className="select" value={r.zone || ""} aria-label={`Zona de ${r.name}`} onChange={(e) => onRates(rates.map((x, k) => (k === i ? { ...x, zone: e.target.value || null } : x)))}>{ZONES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
              </div>
            ))}
            <p className="muted" style={{ fontSize: 12, margin: 0 }}>“Solo GAM” no se ofrece a direcciones de Guanacaste, Puntarenas, Limón ni a cantones claramente fuera de la GAM.</p>
          </div>
        )}
      </div>
    </Card>
  );
}
