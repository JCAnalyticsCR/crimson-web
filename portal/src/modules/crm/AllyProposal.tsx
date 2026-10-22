/* Oportunidad propuesta por un aliado desde su portal (21/10, ítem 16; pregunta abierta 8). Llega «Pendiente de
   aprobación de Crimson» y sin responsable: quien ve todo el embudo la aprueba (y de paso le asigna responsable) o la
   rechaza con un motivo que el aliado ve. Al aliado le llega un correo con la decisión. En una oportunidad de Crimson
   no muestra nada. */
import { useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { useSession } from "../../app/session";

type Proposal = { status: "pendiente" | "aprobada" | "rechazada"; label: string; company: string; by: string | null; created_at: string; lost_reason: string | null };

export function AllyProposalBanner({ oppId, sellers, onChange }: { oppId: number; sellers: { id: number; name: string }[]; onChange: () => void }) {
  const { allows, toast } = useSession();
  const [p, setP] = useState<Proposal | null>(null);
  const [owner, setOwner] = useState("");
  const [rechazo, setRechazo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    api<{ proposal: Proposal | null }>(`/opportunities/${oppId}/ally-proposal`).then((r) => setP(r.proposal)).catch(() => setP(null));
  }, [oppId]);
  useEffect(load, [load]);
  if (!p) return null;
  const decide = allows("crm_pipeline.ver_todo") && allows("crm_pipeline.editar") && p.status === "pendiente";
  const go = async (path: "approve" | "reject", json: Record<string, unknown>) => {
    if (busy) return;
    setBusy(true);
    try {
      await api(`/opportunities/${oppId}/ally-proposal/${path}`, { method: "POST", json });
      toast(path === "approve" ? "Oportunidad aprobada: se le avisó al aliado" : "Oportunidad rechazada: se le avisó al aliado");
      setRechazo(null);
      load();
      onChange();
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo guardar", "bad"); }
    finally { setBusy(false); }
  };
  const tono = p.status === "pendiente" ? "warn" : p.status === "aprobada" ? "ok" : "muted";
  return (
    <div style={{ border: `1px solid var(--${tono === "muted" ? "hair-2" : tono})`, borderRadius: 10, padding: "10px 14px", display: "grid", gap: 8, fontSize: 13 }}>
      <div>
        <span className={`badge badge--${tono}`}>{p.label}</span>{" "}
        Propuesta por <b>{p.company}</b>{p.by ? ` (${p.by})` : ""} desde el portal de aliados · {fmtDate(p.created_at.slice(0, 10))}
        {p.lost_reason && <div className="muted">{p.lost_reason}</div>}
      </div>
      {decide && rechazo === null && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <select className="select select--sm" style={{ maxWidth: 240 }} value={owner} onChange={(e) => setOwner(e.target.value)} aria-label="Responsable en Crimson">
            <option value="">Sin responsable por ahora</option>
            {sellers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <button className="btn btn--crimson btn--sm" disabled={busy} onClick={() => go("approve", owner ? { owner_id: Number(owner) } : {})}>{busy ? "Guardando…" : "Aprobar"}</button>
          <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => setRechazo("")}>Rechazar</button>
        </div>
      )}
      {decide && rechazo !== null && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input className="input" style={{ flex: "1 1 240px" }} autoFocus maxLength={180} placeholder="Motivo (lo ve el aliado)" value={rechazo} onChange={(e) => setRechazo(e.target.value)} />
          <button className="btn btn--ghost btn--sm" disabled={busy} onClick={() => setRechazo(null)}>Cancelar</button>
          <button className="btn btn--crimson btn--sm" disabled={busy || rechazo.trim().length < 3} onClick={() => go("reject", { reason: rechazo.trim() })}>{busy ? "Guardando…" : "Rechazar"}</button>
        </div>
      )}
    </div>
  );
}
