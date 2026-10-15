/* Configuracion de comisiones: los defaults mientras Andres decide (preguntas abiertas de economia). Solo
   commissions.configurar. Nada de esto crea reglas con porcentajes. */
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { roleMeta } from "../../app/roles";
import { Card, Field } from "../../ui/components";
import { TRIGGER, errText, type Config as C } from "./types";

const ROLES = ["ventas", "supervisor", "contabilidad", "caja", "inventario", "tecnico", "lectura", "admin"];

export default function Config() {
  const { toast } = useSession();
  const [c, setC] = useState<C | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<C>("/commissions/config").then(setC).catch((e) => toast(errText(e), "bad")); }, [toast]);
  if (!c) return <div style={{ padding: 40, textAlign: "center" }}><span className="spinner" /></div>;
  const save = async () => {
    setBusy(true);
    try { setC(await api<C>("/commissions/config", { method: "PUT", json: c })); toast("Configuración guardada"); }
    catch (e) { toast(errText(e), "bad"); } finally { setBusy(false); }
  };
  const trig = (v: string, on: (x: string) => void) => <select className="select" value={v} onChange={(e) => on(e.target.value)}>{["cobro", "cotizacion_aceptada", "proyecto_completado"].map((k) => <option key={k} value={k}>{TRIGGER[k]}</option>)}</select>;
  return (
    <Card title="Configuración" extra={<button className="btn btn--crimson btn--sm" disabled={busy} onClick={save}>Guardar</button>}>
      <div className="grid-2">
        <Field label="Cuándo gana un vendedor por defecto" hint="Solo prellena las reglas nuevas.">{trig(c.default_trigger_vendedor, (x) => setC({ ...c, default_trigger_vendedor: x }))}</Field>
        <Field label="Cuándo gana un aliado por defecto" hint="Solo prellena las reglas nuevas.">{trig(c.default_trigger_aliado, (x) => setC({ ...c, default_trigger_aliado: x }))}</Field>
        <Field label="Sobre qué gana un vendedor por defecto"><select className="select" value={c.default_base_vendedor} onChange={(e) => setC({ ...c, default_base_vendedor: e.target.value })}><option value="venta">La venta sin IVA</option><option value="margen">La utilidad</option></select></Field>
        <Field label="Sobre qué gana un aliado por defecto"><select className="select" value={c.default_base_aliado} onChange={(e) => setC({ ...c, default_base_aliado: e.target.value })}><option value="venta">La venta sin IVA</option><option value="margen">La utilidad</option></select></Field>
        <Field label="Cuándo se le debe al aliado su pago acordado" hint="El pago acordado es el precio del aliado que Crimson aprobó.">
          <select className="select" value={c.ally_payout_trigger} onChange={(e) => setC({ ...c, ally_payout_trigger: e.target.value })}><option value="proyecto_completado">Al completar el proyecto</option><option value="cobro">Al cobrar (pago contra pago)</option></select>
        </Field>
      </div>
      <Field label="Roles que comisionan con la regla de «todos los vendedores»" hint="Por defecto solo Vendedor: el administrador no se comisiona a sí mismo por accidente.">
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
          {ROLES.map((r) => (
            <label key={r} className="com-check"><input type="checkbox" checked={c.general_rule_roles.includes(r)} onChange={(e) => setC({ ...c, general_rule_roles: e.target.checked ? [...c.general_rule_roles, r] : c.general_rule_roles.filter((x) => x !== r) })} />{roleMeta(r).label}</label>
          ))}
        </div>
      </Field>
      <label className="com-check"><input type="checkbox" checked={c.show_margin_base} onChange={(e) => setC({ ...c, show_margin_base: e.target.checked })} />Mostrar la base de utilidad a quien no ve costos</label>
      <label className="com-check"><input type="checkbox" checked={c.auto_approve} onChange={(e) => setC({ ...c, auto_approve: e.target.checked })} />Aprobar automáticamente</label>
      {c.auto_approve && <div className="com-warn">Andrés pidió aprobar cada comisión («yo aquí le voy a poder aprobar»). Con esto encendido nacen aprobadas.</div>}
    </Card>
  );
}
