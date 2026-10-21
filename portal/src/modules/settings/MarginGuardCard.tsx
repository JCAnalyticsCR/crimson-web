/* Ajustes → Facturación → Margen de la tienda (plan 1.12). El guardian de margen corre solo despues de cada refresco
   de Eurocomp y de cada tipo de cambio nuevo: lo que queda bajo el minimo pasa a "Consultar" en la tienda y entra a
   Aprobaciones → Precios por revisar con el precio sugerido. Nunca cambia un precio solo. */
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Field } from "../../ui/components";

export type GuardLast = {
  mode: "informe" | "activo"; at: string; reason: string; evaluados: number; bajo_minimo: number; retenidos: number;
  nuevos_retenidos: number; liberados: number; dudosos: number; sobre_tope: number; sin_tasa: number; sin_costo: number;
};
export type MarginCfg = {
  min_margin_pct?: number; default_margin_pct?: number; margin_guard_mode?: "informe" | "activo" | null; margin_guard_cap?: number; margin_guard_jump_pct?: number;
  margin_guard_last?: GuardLast | null;
};

const MODO: Record<string, string> = {
  informe: "Solo informe: avisa y propone el precio, no saca nada de venta",
  activo: "Activo: lo que queda bajo el mínimo pasa a «Consultar» hasta que apliqués un precio",
};

export default function MarginGuardCard({ cfg, save }: { cfg: MarginCfg; save: (patch: MarginCfg) => void }) {
  const { allows, toast } = useSession();
  const [last, setLast] = useState<GuardLast | null>(null); // la de "Revisar ahora" (guardar ajustes trae la suya en cfg)
  const [busy, setBusy] = useState(false);
  const edita = allows("settings.configurar");
  const revisa = allows("catalog.editar") && allows("catalog.costos");
  const modo = cfg.margin_guard_mode ?? null;
  const run = async () => {
    setBusy(true);
    try {
      const r = await api<GuardLast>("/approvals/prices/run", { method: "POST" });
      setLast(r);
      toast(r.bajo_minimo ? `${r.bajo_minimo} producto(s) bajo el mínimo: revisalos en Aprobaciones` : "Todos los productos de la tienda están sobre el mínimo");
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  const shown = [last, cfg.margin_guard_last ?? null].filter((x): x is GuardLast => !!x).sort((a, b) => b.at.localeCompare(a.at))[0] ?? null;
  return (
    <Card title="Margen de la tienda">
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div className="grid-2">
          <Field label="Margen mínimo (%)" hint="Bajo este margen sobre la venta, el producto se revisa (y en modo activo sale de venta). También pide aprobación en cotizaciones.">
            <input className="input input--mono" type="number" min={0} max={95} disabled={!edita} defaultValue={cfg.min_margin_pct ?? 25} onBlur={(e) => e.target.value.trim() && Number(e.target.value) !== Number(cfg.min_margin_pct ?? 25) && save({ min_margin_pct: Number(e.target.value) })} />
          </Field>
          <Field label="Margen objetivo del precio sugerido (%)" hint="El del producto si lo tiene; si no, este. Nunca menos que el mínimo.">
            <input className="input input--mono" type="number" min={0} max={95} disabled={!edita} defaultValue={cfg.default_margin_pct ?? 35} onBlur={(e) => e.target.value.trim() && Number(e.target.value) !== Number(cfg.default_margin_pct ?? 35) && save({ default_margin_pct: Number(e.target.value) })} />
          </Field>
          <Field label="Modo" hint={modo ? undefined : "Todavía no corrió: la primera revisión es solo informe y después queda activo."}>
            <select className="select" disabled={!edita} value={modo ?? ""} onChange={(e) => e.target.value && save({ margin_guard_mode: e.target.value as "informe" | "activo" })}>
              {!modo && <option value="">Primera revisión: solo informe</option>}
              <option value="informe">{MODO.informe}</option>
              <option value="activo">{MODO.activo}</option>
            </select>
          </Field>
          <Field label="Tope por revisión (productos)" hint="Una revisión no saca de venta más que esto (un tipo de cambio mal digitado no vacía la tienda).">
            <input className="input input--mono" type="number" min={1} max={1000} disabled={!edita} defaultValue={cfg.margin_guard_cap ?? 20} onBlur={(e) => e.target.value.trim() && Number(e.target.value) !== Number(cfg.margin_guard_cap ?? 20) && save({ margin_guard_cap: Number(e.target.value) })} />
          </Field>
          <Field label="Salto de costo «dudoso» (%)" hint="Si el costo cambia esto o más de una vez, el aviso pide verificarlo con el proveedor antes de aplicar (posible error de la lista).">
            <input className="input input--mono" type="number" min={1} max={500} disabled={!edita} defaultValue={cfg.margin_guard_jump_pct ?? 25} onBlur={(e) => e.target.value.trim() && Number(e.target.value) > 0 && Number(e.target.value) !== Number(cfg.margin_guard_jump_pct ?? 25) && save({ margin_guard_jump_pct: Number(e.target.value) })} />
          </Field>
        </div>
        {shown ? (
          <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
            Última revisión ({shown.reason || "automática"}, {new Date(shown.at).toLocaleString("es-CR")}, modo {shown.mode}): {shown.evaluados} evaluados, <b>{shown.bajo_minimo} bajo el mínimo</b>, {shown.retenidos} fuera de venta
            {shown.dudosos ? `, ${shown.dudosos} con un salto de costo dudoso` : ""}{shown.sobre_tope ? `, ${shown.sobre_tope} sobre el tope` : ""}
            {shown.sin_tasa ? `, ${shown.sin_tasa} sin tipo de cambio` : ""}{shown.sin_costo ? `, ${shown.sin_costo} sin costo` : ""}.
          </p>
        ) : <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Todavía no hay revisiones. Corre sola después de cada refresco de Eurocomp y de cada tipo de cambio nuevo.</p>}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {revisa && <button className="btn btn--soft btn--sm" onClick={run} disabled={busy}>{busy ? "Revisando…" : "Revisar ahora"}</button>}
          <Link className="btn btn--ghost btn--sm" to="/aprobaciones#precios_por_revisar">Aprobaciones → Precios por revisar</Link>
        </div>
        <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>El sistema nunca cambia un precio solo: propone el precio (costo × tipo de cambio ÷ (1 − margen objetivo), redondeado a la centena) y lo aplicás con un clic.</p>
      </div>
    </Card>
  );
}
