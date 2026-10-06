/* Revision del supervisor: entre "enviado a oficina" y el costeo. El supervisor (o admin) aprueba o devuelve con
   observaciones por punto o generales; el tecnico las ve, las marca resueltas y reenvia (vuelve a revision). */
import { useState } from "react";
import { api, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { I, Icon } from "../../ui/components";

export type Observation = {
  id: number; round: number; point_code: string | null; text: string; created_by: string | null; created_at: string;
  resolved: boolean; resolved_at: string | null; resolved_by: string | null; resolution: string | null;
};
export type ReviewInfo = {
  review_status: "pendiente" | "aprobado" | "devuelto" | null; review_label: string; reviewed_by: string | null; reviewed_at: string | null;
  review_round: number; open_observations: number; observations: Observation[];
};

export const pickReview = (s: Partial<ReviewInfo>): ReviewInfo => ({
  review_status: s.review_status ?? null, review_label: s.review_label || "Sin enviar", reviewed_by: s.reviewed_by ?? null, reviewed_at: s.reviewed_at ?? null,
  review_round: s.review_round ?? 0, open_observations: s.open_observations ?? 0, observations: s.observations ?? [],
});

const TONE: Record<string, string> = { pendiente: "info", aprobado: "ok", devuelto: "warn" };
export const reviewTone = (st: string | null | undefined) => (st ? TONE[st] || "muted" : "muted");
const when = (s: string | null) => (s ? parseTs(s).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "");

type Row = { point_code: string; text: string };

export function ReviewPanel({ surveyId, status, info, pointCodes, onChange }: {
  surveyId: number; status: string | undefined; info: ReviewInfo; pointCodes: string[];
  /** respuesta completa del levantamiento: el editor toma de ahi solo la revision y el estado (no pisa lo no guardado) */
  onChange: (s: Partial<ReviewInfo> & { status: string }) => void;
}) {
  const { toast, allows } = useSession();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const canReview = allows("field.revisar");
  const canResolve = allows("field.editar");
  const waiting = status === "enviado" && (info.review_status === "pendiente" || info.review_status === null);

  const review = async (action: "aprobar" | "devolver") => {
    const obs = (rows || []).filter((r) => r.text.trim()).map((r) => ({ point_code: r.point_code || null, text: r.text.trim() }));
    if (action === "devolver" && !obs.length) return toast("Escribí al menos una observación para devolverlo", "bad");
    setBusy(true);
    try {
      const s = await api<Partial<ReviewInfo> & { status: string }>(`/surveys/${surveyId}/review`, { method: "POST", json: { action, observations: action === "devolver" ? obs : [] } });
      toast(action === "aprobar" ? "Revisión aprobada: ya se puede costear y cotizar" : "Devuelto al técnico con observaciones (le avisamos por correo)");
      setRows(null); onChange(s);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  const resolve = async (o: Observation, resolved: boolean) => {
    try {
      const s = await api<Partial<ReviewInfo> & { status: string }>(`/surveys/${surveyId}/observations/${o.id}`, { method: "POST", json: { resolved, resolution: notes[o.id] || null } });
      onChange(s);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const open = info.observations.filter((o) => !o.resolved);
  const done = info.observations.filter((o) => o.resolved);
  return (
    <section className={`review review--${reviewTone(info.review_status)}`} aria-label="Revisión del supervisor">
      <div className="review__head">
        <span className={`badge badge--${reviewTone(info.review_status)}`}>{info.review_label}</span>
        <span className="muted">
          {info.reviewed_by ? `${info.review_status === "pendiente" ? "Última revisión" : "Por"} ${info.reviewed_by} · ${when(info.reviewed_at)}` : waiting ? "Esperando al supervisor" : ""}
          {info.review_round > 1 ? ` · ronda ${info.review_round}` : ""}
        </span>
      </div>
      {info.review_status === "devuelto" && open.length > 0 && <p className="review__hint">Corregí lo que pide el supervisor, marcá cada observación como resuelta y reenviá a oficina.</p>}
      {info.review_status === "devuelto" && open.length === 0 && <p className="review__hint">Todas las observaciones están resueltas: ya podés reenviar a oficina.</p>}

      {open.length > 0 && <ul className="review__list">{open.map((o) => (
        <li key={o.id}>
          <div><span className="review__pt">{o.point_code || "General"}</span>{o.text}</div>
          <div className="muted review__meta">{o.created_by} · {when(o.created_at)}{o.round > 1 ? ` · ronda ${o.round}` : ""}</div>
          {canResolve && status !== "cotizado" && status !== "cerrado" && (
            <div className="review__resolve">
              <input className="input" placeholder="Qué se corrigió (opcional)" value={notes[o.id] || ""} onChange={(e) => setNotes({ ...notes, [o.id]: e.target.value })} />
              <button className="btn btn--soft btn--sm" onClick={() => resolve(o, true)}><Icon d={I.check} size={14} />Marcar resuelta</button>
            </div>
          )}
        </li>
      ))}</ul>}
      {done.length > 0 && (
        <details className="review__done">
          <summary>{done.length} resuelta{done.length !== 1 ? "s" : ""}</summary>
          <ul className="review__list">{done.map((o) => (
            <li key={o.id} className="is-done">
              <div><span className="review__pt">{o.point_code || "General"}</span>{o.text}</div>
              <div className="muted review__meta">Resuelta por {o.resolved_by} · {when(o.resolved_at)}{o.resolution ? ` · «${o.resolution}»` : ""}</div>
              {canResolve && status === "borrador" && <button className="btn btn--ghost btn--sm" onClick={() => resolve(o, false)}>Reabrir</button>}
            </li>
          ))}</ul>
        </details>
      )}

      {canReview && waiting && (rows === null ? (
        <div className="review__act">
          <button className="btn btn--crimson btn--sm" disabled={busy} onClick={() => review("aprobar")}><Icon d={I.check} size={14} />Aprobar revisión</button>
          <button className="btn btn--soft btn--sm" disabled={busy} onClick={() => setRows([{ point_code: "", text: "" }])}>Devolver con observaciones</button>
        </div>
      ) : (
        <div className="review__form">
          {rows.map((r, i) => (
            <div className="review__row" key={i}>
              <select className="select select--sm" value={r.point_code} aria-label="Punto" onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, point_code: e.target.value } : x)))}>
                <option value="">General</option>{pointCodes.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <textarea className="textarea" rows={2} placeholder="Qué falta o qué hay que corregir" value={r.text} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))} />
              <button className="btn btn--ghost btn--sm" aria-label="Quitar" onClick={() => setRows(rows.length > 1 ? rows.filter((_, j) => j !== i) : [{ point_code: "", text: "" }])}><Icon d={I.x} size={14} /></button>
            </div>
          ))}
          <div className="review__act">
            <button className="btn btn--ghost btn--sm" onClick={() => setRows([...rows, { point_code: "", text: "" }])}><Icon d={I.plus} size={14} />Otra observación</button>
            <span style={{ flex: 1 }} />
            <button className="btn btn--ghost btn--sm" onClick={() => setRows(null)}>Cancelar</button>
            <button className="btn btn--crimson btn--sm" disabled={busy} onClick={() => review("devolver")}>Devolver al técnico</button>
          </div>
        </div>
      ))}
    </section>
  );
}
