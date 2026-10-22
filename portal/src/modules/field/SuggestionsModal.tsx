/* «Materiales y equipos sugeridos»: la lista que propone la API a partir de los puntos; el usuario marca qué agregar.
   La usan el levantamiento interno (Surveys.tsx, con «Ver equipos» de la recomendación) y el del aliado (AllySurvey.tsx,
   sin equipos, proveedores ni precios). Reunión 21/10, ítem 24: «Seleccionar todo» / «Deseleccionar todo» y lista
   compacta, sin fotos. Ítem 25: «Agregar al levantamiento» se bloquea mientras guarda (un doble clic no suma dos veces)
   y el error se muestra en el mismo modal. */
import type { ReactNode } from "react";
import { Modal } from "../../ui/components";
import "./suggestions.css";

export type SugAction = "nuevo" | "sumar" | "cubierto" | "revisar";
export type SugBase = {
  name: string; quantity: string | number; unit: string; kind: string; reason: string; action: SugAction;
  existing_quantity: string | number; add_quantity: string | number; item_id: number | null; matched_name?: string;
  missing?: string[]; estimated?: string[]; warnings?: string[]; reference?: boolean;
  alternatives?: { label: string; reserva: number; actual: boolean }[];
  /** false: la línea se agrega eligiendo un equipo real («Ver equipos»), no la genérica */
  apply_generic?: boolean;
};
export type SugRow<T extends SugBase = SugBase> = T & { checked: boolean; qty: string };

const ACTION: Record<SugAction, { label: string; tone: string }> = {
  nuevo: { label: "Nuevo", tone: "ok" }, sumar: { label: "Ya está: sumar", tone: "info" },
  cubierto: { label: "Ya alcanza", tone: "muted" }, revisar: { label: "Revisar cantidad", tone: "warn" },
};
const num = (v: string | number | null | undefined) => { const n = Number(String(v ?? "").replace(",", ".")); return Number.isFinite(n) ? n : 0; };

/* lo nuevo marcado; la línea de discos (TB) se agrega solo eligiendo un disco real */
export const toSugRow = <T extends SugBase>(x: T): SugRow<T> => ({
  ...x,
  checked: x.action === "nuevo" && x.apply_generic !== false,
  qty: x.action === "revisar" || x.apply_generic === false ? "" : String(num(x.action === "sumar" ? x.add_quantity : x.quantity) || ""),
});
export const chosenSugs = <T extends SugBase>(rows: SugRow<T>[]) => rows.filter((x) => x.checked && num(x.qty) > 0);
export const sugPayload = <T extends SugBase>(rows: SugRow<T>[]) => chosenSugs(rows).map((x) => ({ name: x.name, quantity: num(x.qty), unit: x.unit, kind: x.kind, item_id: x.item_id }));

export function SuggestionsModal<T extends SugBase>({ rows, onRows, onApply, onClose, busy, error, solutionLabel, noPoints, extra }: {
  rows: SugRow<T>[]; onRows: (rows: SugRow<T>[]) => void; onApply: () => void; onClose: () => void;
  busy: boolean; error?: string | null; solutionLabel: string; noPoints: boolean;
  /** debajo del nombre: el equipo recomendado y «Ver equipos» (solo el levantamiento interno) */
  extra?: (row: SugRow<T>) => ReactNode;
}) {
  const marcables = rows.filter((x) => x.apply_generic !== false);
  const todos = marcables.length > 0 && marcables.every((x) => x.checked);
  const setAll = (on: boolean) => onRows(rows.map((x) => (x.apply_generic === false ? x : { ...x, checked: on, qty: on && num(x.qty) <= 0 ? String(num(x.action === "sumar" ? x.add_quantity : x.quantity) || "") : x.qty })));
  const n = chosenSugs(rows).length;
  return (
    <Modal title="Materiales y equipos sugeridos" onClose={busy ? () => undefined : onClose} wide foot={<>
      <span className="muted" style={{ marginRight: "auto", fontSize: 13 }}>{n} para agregar · lo que ya cargó no se borra</span>
      <button className="btn btn--ghost" onClick={onClose} disabled={busy}>Cancelar</button>
      <button className="btn btn--crimson" onClick={onApply} disabled={busy || n === 0} aria-busy={busy}>{busy ? "Agregando…" : "Agregar al levantamiento"}</button>
    </>}>
      {/* 13/10 33:45-34:10: sugerir propone; agregar lo pasa al levantamiento; la cotización sale después */}
      <ol className="sug-flow" aria-label="Cómo llegan los materiales a la cotización">
        <li><b>1</b> Sugerir: el sistema propone según los puntos (no cambia nada)</li>
        <li><b>2</b> Agregar al levantamiento: pasa a «Equipos y materiales»</li>
        <li><b>3</b> Revisión, costeo y cotización: la cotización sale con esas líneas</li>
      </ol>
      {noPoints
        ? <p className="sug-note sug-note--warn">Todavía no hay puntos levantados, así que no se pueden calcular cantidades. Estos son los materiales habituales de <b>{solutionLabel}</b>: escriba la cantidad de los que vaya a usar.</p>
        : <p className="muted sug-note">Salen de los puntos levantados, sus observaciones y lo que ya está en la lista. Marque lo que quiere agregar y ajuste la cantidad; si el material ya existe se suma a esa línea.</p>}
      {error && <p className="sug-note sug-note--bad" role="alert">{error}</p>}
      <div className="sug-tools">
        <button type="button" className="btn btn--ghost btn--sm" disabled={busy || todos} onClick={() => setAll(true)}>Seleccionar todo</button>
        <button type="button" className="btn btn--ghost btn--sm" disabled={busy || !rows.some((x) => x.checked)} onClick={() => setAll(false)}>Deseleccionar todo</button>
      </div>
      <table className="table sug-table">
        <thead><tr><th style={{ width: 32 }} /><th>Sugerencia</th><th>Bloque</th><th className="num">Ya cargado</th><th className="num">Agregar</th><th>Por qué</th></tr></thead>
        <tbody>{rows.map((x, i) => (
          <tr key={x.name} style={{ opacity: x.action === "cubierto" && !x.checked ? 0.6 : 1 }}>
            <td><input type="checkbox" aria-label={`Agregar ${x.name}`} checked={x.checked} disabled={busy || x.apply_generic === false} title={x.apply_generic === false ? "Se agrega eligiendo un equipo real: «Ver equipos»" : undefined} onChange={(e) => onRows(rows.map((y, j) => (j === i ? { ...y, checked: e.target.checked } : y)))} /></td>
            <td>
              <b>{x.name}</b> <span className={`badge badge--${ACTION[x.action].tone}`}>{ACTION[x.action].label}</span>
              {extra?.(x)}
            </td>
            <td className="muted" style={{ textTransform: "capitalize" }}>{x.kind}</td>
            <td className="num mono">{num(x.existing_quantity) ? num(x.existing_quantity) : "—"}</td>
            <td className="num"><input className="input input--mono" inputMode="decimal" style={{ maxWidth: 80 }} value={x.qty} placeholder="0" disabled={busy || x.apply_generic === false} onChange={(e) => onRows(rows.map((y, j) => (j === i ? { ...y, qty: e.target.value, checked: num(e.target.value) > 0 ? true : y.checked } : y)))} /> <span className="muted" style={{ fontSize: 12 }}>{x.unit}</span></td>
            <td className="muted sug-why">
              {x.reference && <span className="badge badge--warn" style={{ marginRight: 6 }}>referencia del curso</span>}
              {x.reason}{x.matched_name && <div>Se suma a «{x.matched_name}», que ya está cargado.</div>}
              {x.warnings?.map((w) => <div key={w} className="sug__warn">⚠ {w}</div>)}
              {x.alternatives && x.alternatives.length > 1 && (
                <ul className="sug__alts" title="Elija la combinación y ajuste la cantidad">
                  {x.alternatives.map((o) => <li key={o.label}>{o.label} · reserva {o.reserva}{o.actual ? " (sugerida)" : ""}</li>)}
                </ul>
              )}
            </td>
          </tr>
        ))}</tbody>
      </table>
    </Modal>
  );
}
