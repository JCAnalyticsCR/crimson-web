/* Reglas (13/10, 27:25: "dice que no hay reglas"). Sin reglas nadie comisiona: las plantillas solo prellenan el
   formulario, el % lo decide Andres. Editar y borrar solo con commissions.configurar. */
import { useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, I, Icon } from "../../ui/components";
import RuleModal, { blankRule, ruleToForm, type RuleForm } from "./RuleModal";
import { SOLUTION, errText, type Beneficiaries, type Config, type Rule } from "./types";

const TIPO: Record<string, string> = { comision: "Comisión", bono_fin: "Bono por completar", bono_anticipo: "Bono por terminar antes" };

export default function Rules() {
  const { toast, allows, me } = useSession();
  const configura = allows("commissions.configurar");
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [cfg, setCfg] = useState<Config | null>(null);
  const [ben, setBen] = useState<Beneficiaries | null>(null);
  const [form, setForm] = useState<RuleForm | null>(null);
  const load = useCallback(() => { api<Rule[]>("/commissions/rules").then(setRules).catch((e) => toast(errText(e), "bad")); }, [toast]);
  useEffect(() => {
    load();
    api<Config>("/commissions/config").then(setCfg).catch(() => setCfg(null));
    if (configura) api<Beneficiaries>("/commissions/beneficiaries").then(setBen).catch(() => setBen(null));
  }, [load, configura]);
  const margin = Number((me?.tenant as unknown as { settings?: { default_margin_pct?: number } })?.settings?.default_margin_pct ?? 35);

  const borrar = async (r: Rule) => {
    if (!confirm(`¿Eliminar la regla «${r.name}»?`)) return;
    try { await api(`/commissions/rules/${r.id}`, { method: "DELETE" }); toast("Regla eliminada"); load(); }
    catch (e) { toast(errText(e), "bad"); }
  };
  const plantillas: [string, () => RuleForm][] = [
    ["Vendedores · % de lo cobrado sin IVA", () => ({ ...blankRule(cfg, "vendedor"), name: "Vendedores · % de lo cobrado", base: "venta", trigger: "cobro" })],
    ["Aliados · % de la venta del proyecto, al cobrar", () => ({ ...blankRule(cfg, "aliado"), name: "Aliados · % de la venta", base: "venta", trigger: "cobro" })],
    ["Bono por completar el proyecto (aliados)", () => blankRule(cfg, "aliado", "bono_fin")],
  ];

  return (
    <>
      {configura && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
          <button className="btn btn--crimson btn--sm" onClick={() => setForm(blankRule(cfg))}><Icon d={I.plus} />Nueva regla</button>
        </div>
      )}
      <Card title="Reglas" flush extra={<span className="meta">gana la más específica · no se suman</span>}>
        {!rules ? <div style={{ padding: 24, textAlign: "center" }}><span className="spinner" /></div> : rules.length === 0 ? (
          <Empty
            title="Sin reglas: nadie comisiona todavía"
            hint={configura ? "Empezá con una plantilla: solo prellena el formulario, el porcentaje lo decidís vos." : "Administración carga las reglas."}
            action={configura ? <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "center" }}>{plantillas.map(([l, mk]) => <button key={l} className="btn btn--soft btn--sm" onClick={() => setForm(mk())}>{l}</button>)}</div> : undefined}
          />
        ) : (
          <div className="com-scroll">
            <table className="table">
              <thead><tr><th>Quién</th><th>Tipo</th><th>Regla</th><th>Filtros</th><th>Vigencia</th><th>Activa</th><th /></tr></thead>
              <tbody>{rules.map((r) => (
                <tr key={r.id}>
                  <td style={{ fontWeight: 600 }}>{r.user || "—"}<div className="meta">{r.beneficiary === "aliado" ? (r.project ? `Acuerdo · ${r.project}` : "Aliado") : "Vendedor"}</div></td>
                  <td>{TIPO[r.kind] || r.kind}</td>
                  <td>{r.summary}<div className="muted" style={{ fontSize: 12 }}>{r.name}</div></td>
                  <td className="muted">{[r.deal_kind === "venta" ? "Venta de equipo" : r.deal_kind === "proyecto" ? "Proyectos" : null, r.solution ? SOLUTION[r.solution] : null].filter(Boolean).join(" · ") || "—"}</td>
                  <td className="muted">{r.valid_from || r.valid_to ? `${r.valid_from ? fmtDate(r.valid_from) : "siempre"} → ${r.valid_to ? fmtDate(r.valid_to) : "sin fin"}` : "—"}</td>
                  <td>{r.active ? <span className="badge badge--ok">Sí</span> : <span className="badge badge--muted">No</span>}</td>
                  <td className="num" style={{ whiteSpace: "nowrap" }}>
                    {configura && !r.participation_id && <button className="btn btn--ghost btn--sm" onClick={() => setForm(ruleToForm(r))}>Editar</button>}
                    {configura && !r.used && <button className="btn btn--ghost btn--sm" onClick={() => borrar(r)} aria-label={`Eliminar ${r.name}`}><Icon d={I.x} size={14} /></button>}
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
      <p className="com-note">Los acuerdos de un proyecto con un aliado (comisión propia y bonos) se editan desde el proyecto o la oportunidad: «Acuerdo económico».</p>
      {form && <RuleModal initial={form} ben={ben} defaultMargin={margin} onClose={() => setForm(null)} onSaved={() => { setForm(null); load(); }} />}
    </>
  );
}
