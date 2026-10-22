/* "Asignar a técnico de aliado": el levantamiento queda a cargo de la empresa aliada que participa en la oportunidad
   (lo ve y lo llena desde su portal). Elegir un técnico de Crimson lo devuelve a Crimson. Lo valida la API
   (POST /surveys/{id}/assign con check_ally_assignment). */
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field } from "../../ui/components";

type Tech = { id: number; name: string; role: string; company?: string | null };

export function AssignAllySurvey({ surveyId, opportunityId, current, onDone }: { surveyId: number; opportunityId: number; current?: string | null; onDone: () => void }) {
  const { toast } = useSession();
  const [open, setOpen] = useState(false);
  const [techs, setTechs] = useState<Tech[]>([]);
  const [sel, setSel] = useState("");
  useEffect(() => { if (open) api<Tech[]>(`/field/technicians?opportunity_id=${opportunityId}`).then(setTechs).catch(() => setTechs([])); }, [open, opportunityId]);
  const asignar = async () => {
    try { await api(`/surveys/${surveyId}/assign`, { method: "POST", json: { technician_id: Number(sel) } }); toast("Levantamiento asignado"); setOpen(false); onDone(); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo asignar", "bad"); }
  };
  return (
    <div style={{ margin: "6px 0 10px" }}>
      {current && <span className="badge badge--info" style={{ marginRight: 8 }}>Aliado: {current}</span>}
      {!open ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => setOpen(true)}>Asignar a técnico de aliado</button> : (
        <div style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}>
          <Field label="Técnico">
            <select className="select" value={sel} onChange={(e) => setSel(e.target.value)}>
              <option value="">Elija…</option>
              {techs.map((t) => <option key={t.id} value={t.id}>{t.company ? `${t.name} · ${t.company}` : `${t.name} · Crimson`}</option>)}
            </select>
          </Field>
          <button type="button" className="btn btn--crimson btn--sm" disabled={!sel} onClick={asignar}>Asignar</button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setOpen(false)}>Cancelar</button>
          {!techs.some((t) => t.company) && <span className="meta">Ninguna empresa aliada con técnicos participa en esta oportunidad.</span>}
        </div>
      )}
    </div>
  );
}
