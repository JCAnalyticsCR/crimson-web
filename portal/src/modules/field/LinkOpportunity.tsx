// Ligar un levantamiento ya hecho a una oportunidad (o desligarlo).
// Andres: "hice el levantamiento en el sitio antes de registrar la oportunidad; quiero asignarlo despues".
// Funciona aunque el levantamiento ya este cotizado: la oportunidad hereda cliente, cotizacion, monto y etapa.
import { useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Lookup, type LookupItem } from "../../ui/Lookup";

export type OppRef = { id: number; number: string; title: string; status: string } | null;

const ABIERTAS = new Set(["nuevo", "contactado", "requiere_visita", "levantamiento", "cotizando", "enviada", "negociacion"]);

type OppRow = { id: number; number: string; title: string; status: string; customer?: string | null };

export function LinkOpportunity({ surveyId, current, onChange }: { surveyId: number; current: OppRef; onChange: (o: OppRef) => void }) {
  const { toast, allows } = useSession();
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const puede = allows("field.editar") && allows("crm_pipeline.ver");

  const buscar = async (q: string): Promise<LookupItem[]> => {
    const rows = await api<OppRow[]>(`/opportunities?limit=20${q ? `&q=${encodeURIComponent(q)}` : ""}`);
    return rows
      .filter((o) => ABIERTAS.has(o.status) && o.id !== current?.id)
      .map((o) => ({ id: o.id, label: `${o.number} · ${o.title}`, hint: o.customer || undefined }));
  };

  const ligar = async (id: number | null) => {
    setBusy(true);
    try {
      const s = await api<{ opportunity: OppRef }>(`/surveys/${surveyId}/opportunity`, { method: "POST", json: { opportunity_id: id } });
      onChange(s.opportunity);
      setPicking(false);
      toast(id ? `Ligado a ${s.opportunity?.number}` : "Levantamiento desligado");
    } catch (e) {
      toast((e as Error).message || "No se pudo ligar", "bad");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="link-opp">
      <span className="meta">Oportunidad</span>
      {current && !picking && (
        <div className="link-opp__row">
          <b>{current.number}</b><span>{current.title}</span>
          {puede && <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => setPicking(true)}>Cambiar</button>}
          {puede && <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => ligar(null)}>Quitar</button>}
        </div>
      )}
      {!current && !picking && (
        <div className="link-opp__row">
          <span className="muted">Sin oportunidad ligada.</span>
          {puede && <button type="button" className="btn btn--soft btn--sm" onClick={() => setPicking(true)}>Ligar a una oportunidad</button>}
        </div>
      )}
      {picking && (
        <div className="link-opp__row">
          <div style={{ flex: 1, minWidth: 0 }}>
            <Lookup value="" placeholder="Buscar oportunidad por nombre o número…" fetcher={buscar} disabled={busy} onSelect={(it) => it && ligar(it.id)} />
          </div>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setPicking(false)}>Cancelar</button>
        </div>
      )}
      {picking && <p className="muted" style={{ fontSize: 12, margin: 0 }}>La oportunidad toma el cliente, la cotización y el monto de este levantamiento, y queda anotado en su bitácora.</p>}
    </div>
  );
}
