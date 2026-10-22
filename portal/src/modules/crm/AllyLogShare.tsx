/* Bitácora de la oportunidad: interruptor "Visible para el aliado" por entrada (reunión 2, 21/10, pedido 15).
   Por defecto todo queda oculto; lo que se prende lo ven en su portal las empresas aliadas que ven esta oportunidad.
   Solo aparece si hay alguna (si no, la bitácora se ve igual que siempre). */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";

type State = { can_share: boolean; companies: string[]; shared: string[] };

/** Estado de lo compartido de la oportunidad abierta. `version` cambia cuando cambia la bitácora (recarga). */
export function useAllyLog(oppId: number | null, version: string | null) {
  const [st, setSt] = useState<State | null>(null);
  useEffect(() => {
    if (!oppId) { setSt(null); return; }
    let vivo = true;
    api<State>(`/opportunities/${oppId}/bitacora-aliado`).then((x) => { if (vivo) setSt(x); }).catch(() => { if (vivo) setSt(null); });
    return () => { vivo = false; };
  }, [oppId, version]);
  return [st, setSt] as const;
}

/** La línea tal como está guardada en la bitácora ("fecha · autor: texto"). */
export const rawLine = (l: { when: string; who: string; text: string }) => (l.when ? `${l.when} · ${l.who}: ${l.text}` : l.text);

export function ShareToggle({ oppId, line, st, onChange, canEdit }: { oppId: number; line: string; st: State | null; onChange: (s: State) => void; canEdit: boolean }) {
  const { toast } = useSession();
  const [busy, setBusy] = useState(false);
  const on = !!st?.shared.includes(line);
  const set = useCallback(async (visible: boolean) => {
    setBusy(true);
    try { onChange(await api<State>(`/opportunities/${oppId}/bitacora-aliado`, { method: "PUT", json: { line, visible } })); toast(visible ? "Visible para el aliado" : "Oculta para el aliado"); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo cambiar", "bad"); }
    finally { setBusy(false); }
  }, [oppId, line, onChange, toast]);
  if (!st || (!st.can_share && !on)) return null;
  const quienes = st.companies.join(", ");
  return (
    <label style={{ display: "inline-flex", gap: 6, alignItems: "center", fontSize: 12, color: on ? "var(--crimson)" : "var(--text-3)", cursor: canEdit ? "pointer" : undefined }} title={`Lo ve${st.companies.length > 1 ? "n" : ""} en su portal: ${quienes || "nadie"}`}>
      <input type="checkbox" checked={on} disabled={!canEdit || busy} onChange={(e) => set(e.target.checked)} />
      Visible para el aliado
    </label>
  );
}
