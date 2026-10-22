/* Formulario de una regla: para quien, tipo, sobre que, cuanto, cuando se gana, filtros y vigencia, con un ejemplo
   en vivo. La lista de personas sale de /commissions/beneficiaries (falla I: antes no se podia elegir a un vendedor). */
import { useMemo, useState } from "react";
import { api, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, Modal } from "../../ui/components";
import { SOLUTION, TRIGGER, errText, type Beneficiaries, type Config, type Rule } from "./types";

export type RuleForm = {
  id?: number; beneficiary: "vendedor" | "aliado"; who: string; kind: "comision" | "bono_fin"; name: string; base: "venta" | "margen";
  mode: "percent" | "fixed"; percent: string; fixed_amount: string; fixed_currency: string; trigger: string;
  deal_kind: string; solution: string; valid_from: string; valid_to: string; active: boolean; notes: string;
  /* Reunión 21/10 (pedido 25): al guardar, aplicarla también a lo ya cobrado desde esta fecha */
  apply?: boolean; apply_from?: string;
  whoName?: string | null; // nombre guardado: la persona puede estar desactivada y ya no salir en la lista
};

export function blankRule(cfg: Config | null, beneficiary: "vendedor" | "aliado" = "vendedor", kind: "comision" | "bono_fin" = "comision"): RuleForm {
  return {
    beneficiary, who: "", kind, name: kind === "bono_fin" ? "Bono por completar" : beneficiary === "aliado" ? "Comisión de aliados" : "Comisión de vendedores",
    base: ((beneficiary === "aliado" ? cfg?.default_base_aliado : cfg?.default_base_vendedor) as "venta" | "margen") || "venta",
    mode: kind === "bono_fin" ? "fixed" : "percent", percent: "", fixed_amount: "", fixed_currency: "CRC",
    trigger: kind === "bono_fin" ? "proyecto_completado" : (beneficiary === "aliado" ? cfg?.default_trigger_aliado : cfg?.default_trigger_vendedor) || "cobro",
    deal_kind: "", solution: "", valid_from: "", valid_to: "", active: true, notes: "",
    apply: true, apply_from: primeroDelMes(),
  };
}

/* hoy en Costa Rica (la API corta con esa fecha) y el primer día del mes */
const hoyCR = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Costa_Rica" });
const primeroDelMes = () => `${hoyCR().slice(0, 8)}01`;
const haceUnAnio = () => { const d = new Date(`${hoyCR()}T12:00:00`); d.setFullYear(d.getFullYear() - 1); d.setDate(d.getDate() + 1); return d.toISOString().slice(0, 10); };

export function ruleToForm(r: Rule): RuleForm {
  const fixed = r.fixed_amount != null && Number(r.fixed_amount) > 0;
  return {
    id: r.id, beneficiary: r.beneficiary, who: String((r.beneficiary === "aliado" ? r.ally_company_id : r.user_id) ?? ""), kind: r.kind === "bono_fin" ? "bono_fin" : "comision",
    name: r.name, base: r.base as "venta" | "margen", mode: fixed ? "fixed" : "percent", percent: r.percent ?? "", fixed_amount: r.fixed_amount ?? "",
    fixed_currency: r.fixed_currency || "CRC", trigger: r.trigger, deal_kind: r.deal_kind || "", solution: r.solution || "",
    valid_from: r.valid_from || "", valid_to: r.valid_to || "", active: r.active, notes: r.notes || "",
    apply: false, apply_from: primeroDelMes(),
    whoName: r.beneficiary === "aliado" ? r.ally : r.user,
  };
}

export default function RuleModal({ initial, ben, defaultMargin, onClose, onSaved }: {
  initial: RuleForm; ben: Beneficiaries | null; defaultMargin: number; onClose: () => void; onSaved: () => void;
}) {
  const { toast } = useSession();
  const [f, setF] = useState<RuleForm>(initial);
  const [busy, setBusy] = useState(false);
  const set = (p: Partial<RuleForm>) => setF((x) => ({ ...x, ...p }));
  // la persona o el aliado de la regla ya no esta en la lista (desactivado): se conserva, no pasa a "todos"
  const fuera = !!initial.who && f.who === initial.who && !!ben
    && !(f.beneficiary === "vendedor" ? ben.users.some((u) => String(u.id) === f.who) : ben.allies.some((a) => String(a.id) === f.who));
  const ejemplo = useMemo(() => {
    const venta = 1000000;
    if (f.mode === "fixed") return Number(f.fixed_amount) > 0 ? `Monto fijo: ${fmtMoney(f.fixed_amount, f.fixed_currency)} por negocio${f.trigger === "cobro" ? " (repartido según lo cobrado)" : ""}.` : null;
    const pct = Number(f.percent);
    if (!(pct > 0)) return null;
    if (f.base === "margen") return `En una venta de ₡1 000 000 sin IVA con el margen por defecto de Ajustes (${defaultMargin} %): ${fmtMoney((venta * defaultMargin) / 100 * pct / 100)}.`;
    return `En una venta de ₡1 000 000 sin IVA${f.trigger === "cobro" ? " cobrada completa" : ""}: ${fmtMoney((venta * pct) / 100)}.`;
  }, [f, defaultMargin]);

  const save = async () => {
    const body = {
      beneficiary: f.beneficiary, kind: f.kind, name: f.name.trim() || "Comisión", base: f.base,
      user_id: f.beneficiary === "vendedor" && f.who ? Number(f.who) : null,
      ally_company_id: f.beneficiary === "aliado" && f.who ? Number(f.who) : null,
      percent: f.mode === "percent" ? Number(f.percent || 0) : 0,
      fixed_amount: f.mode === "fixed" ? Number(f.fixed_amount || 0) : null,
      fixed_currency: f.mode === "fixed" ? f.fixed_currency : null,
      trigger: f.kind === "bono_fin" ? "proyecto_completado" : f.trigger,
      deal_kind: f.deal_kind || null, solution: f.solution || null,
      valid_from: f.valid_from || null, valid_to: f.valid_to || null, active: f.active, notes: f.notes || null,
      apply_from: f.apply && f.active && f.apply_from ? f.apply_from : null,
    };
    setBusy(true);
    try {
      const r = await api<{ id: number; backfill?: { creados: number; por_beneficiario: { name: string; count: number; total_crc: string }[] } }>(f.id ? `/commissions/rules/${f.id}` : "/commissions/rules", { method: f.id ? "PUT" : "POST", json: body });
      const b = r.backfill;
      toast(!b ? "Regla guardada" : b.creados ? `Regla guardada · ${b.creados} comisión(es) de ventas anteriores por ${fmtMoney(b.por_beneficiario.reduce((s, x) => s + Number(x.total_crc), 0))}, pendientes de aprobación en la Bandeja` : "Regla guardada · no había ventas anteriores que le correspondan");
      onSaved();
    }
    catch (e) { toast(errText(e), "bad"); } finally { setBusy(false); }
  };

  return (
    <Modal wide title={f.id ? "Editar regla" : "Nueva regla"} onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" disabled={busy} onClick={save}>Guardar</button></>}>
      <div className="grid-2">
        <Field label="Para quién">
          <select className="select" value={f.beneficiary} disabled={!!f.id} onChange={(e) => set({ beneficiary: e.target.value as RuleForm["beneficiary"], who: "" })}><option value="vendedor">Vendedor (persona del equipo)</option><option value="aliado">Aliado (empresa)</option></select>
        </Field>
        {f.beneficiary === "vendedor" ? (
          <Field label="Persona" hint="«Todos los vendedores» solo alcanza a los roles de Configuración (por defecto, Vendedor).">
            <select className="select" value={f.who} onChange={(e) => set({ who: e.target.value })}><option value="">Todos los vendedores</option>{fuera && <option value={f.who}>{f.whoName || "Persona"} (inactiva)</option>}{ben?.users.map((u) => <option key={u.id} value={u.id}>{u.name} · {u.role_label}</option>)}</select>
          </Field>
        ) : (
          <Field label="Empresa aliada" hint="«Todos los aliados» no alcanza a quien contrata a Crimson.">
            <select className="select" value={f.who} onChange={(e) => set({ who: e.target.value })}><option value="">Todos los aliados</option>{fuera && <option value={f.who}>{f.whoName || "Aliado"} (inactivo)</option>}{ben?.allies.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
          </Field>
        )}
        <Field label="Tipo" hint="El bono por terminar antes se pacta en el acuerdo económico de cada proyecto.">
          <select className="select" value={f.kind} onChange={(e) => set({ kind: e.target.value as RuleForm["kind"], trigger: e.target.value === "bono_fin" ? "proyecto_completado" : f.trigger })}><option value="comision">Comisión</option><option value="bono_fin">Bono por completar el proyecto</option></select>
        </Field>
        <Field label="Nombre"><input className="input" maxLength={120} value={f.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field label="Sobre qué">
          <select className="select" value={f.base} onChange={(e) => set({ base: e.target.value as RuleForm["base"] })}><option value="venta">La venta sin IVA</option><option value="margen">La utilidad</option></select>
        </Field>
        <Field label="Cuánto">
          <div style={{ display: "flex", gap: 6 }}>
            <select className="select" style={{ width: 130 }} value={f.mode} onChange={(e) => set({ mode: e.target.value as RuleForm["mode"] })}><option value="percent">Porcentaje</option><option value="fixed">Monto fijo</option></select>
            {f.mode === "percent"
              ? <input className="input input--mono" inputMode="decimal" placeholder="%" value={f.percent} onChange={(e) => set({ percent: e.target.value })} />
              : <><input className="input input--mono" inputMode="decimal" placeholder="Monto" value={f.fixed_amount} onChange={(e) => set({ fixed_amount: e.target.value })} /><select className="select" style={{ width: 90 }} value={f.fixed_currency} onChange={(e) => set({ fixed_currency: e.target.value })}><option>CRC</option><option>USD</option></select></>}
          </div>
        </Field>
        {f.kind === "comision" && (
          <Field label="Cuándo se gana" hint="Las ventas sin proyecto se ganan al cobrar.">
            <select className="select" value={f.trigger} onChange={(e) => set({ trigger: e.target.value })}>{["cobro", "cotizacion_aceptada", "proyecto_completado"].map((k) => <option key={k} value={k}>{TRIGGER[k]}</option>)}</select>
          </Field>
        )}
        <Field label="Solo para (tipo de negocio)">
          <select className="select" value={f.deal_kind} onChange={(e) => set({ deal_kind: e.target.value })}><option value="">Cualquiera</option><option value="venta">Solo venta de equipo</option><option value="proyecto">Solo proyectos</option></select>
        </Field>
        <Field label="Solo para (solución)">
          <select className="select" value={f.solution} onChange={(e) => set({ solution: e.target.value })}><option value="">Cualquiera</option>{Object.entries(SOLUTION).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        </Field>
        <Field label="Vigente desde"><input className="input" type="date" value={f.valid_from} onChange={(e) => set({ valid_from: e.target.value })} /></Field>
        <Field label="Vigente hasta"><input className="input" type="date" value={f.valid_to} onChange={(e) => set({ valid_to: e.target.value })} /></Field>
      </div>
      {f.base === "margen" && <div className="com-warn">Con la comisión y el porcentaje, la persona puede deducir la utilidad. No se le muestra la base ni el porcentaje.</div>}
      {f.base === "margen" && <p className="com-note">Un servicio sin producto no tiene costo: en una venta sin proyecto, las líneas escritas a mano cuentan completas como utilidad. Con proyecto manda la rentabilidad del proyecto al cerrarse.</p>}
      {ejemplo && <div className="com-example">{ejemplo}</div>}
      <label className="com-check"><input type="checkbox" checked={f.active} onChange={(e) => set({ active: e.target.checked })} />Activa</label>
      <Field label="Notas"><input className="input" maxLength={2000} value={f.notes} onChange={(e) => set({ notes: e.target.value })} /></Field>
      <div className="com-example" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <label className="com-check" style={{ margin: 0 }}><input type="checkbox" checked={!!f.apply} disabled={!f.active} onChange={(e) => set({ apply: e.target.checked })} />Aplicarla también a ventas anteriores (lo ya cobrado, aceptado o completado)</label>
        {f.apply && f.active && (
          <Field label="Desde" hint="Máximo un año hacia atrás. Lo que ya tenía comisión no se toca; lo nuevo queda pendiente de aprobación y el vendedor ya lo ve.">
            <input className="input" type="date" value={f.apply_from || ""} min={haceUnAnio()} max={hoyCR()} onChange={(e) => set({ apply_from: e.target.value })} style={{ maxWidth: 200 }} />
          </Field>
        )}
      </div>
      <p className="com-note">Cambiar una regla no recalcula lo ya devengado: guarda la regla con que nació. También podés usar «Aplicar reglas a fechas anteriores» en la Bandeja.</p>
    </Modal>
  );
}
