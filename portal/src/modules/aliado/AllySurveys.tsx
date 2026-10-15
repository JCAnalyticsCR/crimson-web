/* Levantamientos del aliado (14:53-15:27): los de su empresa. El aliado crea uno en una oportunidad (o proyecto) en
   común y lo asigna a sus técnicos; el técnico llena los suyos (borrador o devueltos) y los envía a revisión.
   ?nuevo=1&oportunidad=<id> abre directo el alta (botón "Hacer levantamiento" de la oportunidad). */
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Empty, Field, I, Icon, Modal } from "../../ui/components";
import { ALLY_FIELD_API } from "../field/fieldApi";
import { errorText, useAlly, type OppRow, type SurveyRow } from "./shared";

const KINDS: Record<string, string> = { cctv: "CCTV", redes: "Redes", acceso: "Control de acceso", asistencia: "Asistencia", ups: "UPS", cableado: "Cableado", anpr: "Lectura de placas", otro: "Otro" };

function NewSurvey({ oppId, onClose }: { oppId: number | null; onClose: () => void }) {
  const nav = useNavigate();
  const { toast } = useSession();
  const [opps, setOpps] = useState<OppRow[]>([]);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<number | null>(oppId);
  const [kind, setKind] = useState("cctv");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => api<OppRow[]>(`/aliado/oportunidades?estado=abiertas&q=${encodeURIComponent(q)}`).then((r) => setOpps(r.filter((o) => o.kind !== "venta"))).catch(() => setOpps([])), 200);
    return () => clearTimeout(t);
  }, [q]);
  const crear = async () => {
    if (!sel) return;
    setBusy(true);
    try {
      const s = await api<{ id: number }>(ALLY_FIELD_API.createSurvey, { method: "POST", json: { opportunity_id: sel, kind } });
      onClose();
      nav(`/levantamientos/${s.id}`);
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo crear", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <Modal title="Nuevo levantamiento" onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" disabled={!sel || busy} onClick={crear}><Icon d={I.check} />Crear</button></>}>
      <Field label="Oportunidad en común">
        <input className="input" placeholder="Buscar oportunidad" value={q} onChange={(e) => setQ(e.target.value)} />
      </Field>
      <div className="cp-list" style={{ maxHeight: 260, overflowY: "auto", marginBottom: 12 }}>
        {opps.map((o) => (
          <button key={o.id} type="button" className="cp-row" style={{ textAlign: "left", cursor: "pointer", borderColor: sel === o.id ? "var(--crimson)" : undefined }} onClick={() => setSel(o.id)}>
            <div className="cp-row__main"><b>{o.number} · {o.title}</b><div className="cp-row__meta">{o.customer || "—"}{o.site ? ` · ${o.site}` : ""}</div></div>
          </button>
        ))}
        {!opps.length && <p className="muted">No hay oportunidades abiertas en común para levantar.</p>}
      </div>
      <Field label="Tipo de levantamiento">
        <select className="select" value={kind} onChange={(e) => setKind(e.target.value)}>{Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
      </Field>
    </Modal>
  );
}

export default function AllySurveys() {
  const { can, readOnly } = useAlly();
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<SurveyRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [review, setReview] = useState("");
  const nuevo = params.get("nuevo") === "1" && can("levantamientos_crear") && !readOnly;
  useEffect(() => {
    setRows(null);
    api<SurveyRow[]>(`${ALLY_FIELD_API.surveys}${review ? `?review=${review}` : ""}`).then(setRows).catch((e) => setErr(errorText(e)));
  }, [review]);
  return (
    <>
      <div className="cp-h">
        <h1 className="h1">Levantamientos</h1>
        {can("levantamientos_crear") && !readOnly && <button className="btn btn--crimson" onClick={() => setParams({ nuevo: "1" })}><Icon d={I.plus} />Nuevo</button>}
      </div>
      <div className="cp-seg">
        {[["", "Todos"], ["pendiente", "En revisión"], ["devuelto", "Devueltos"], ["aprobado", "Aprobados"]].map(([k, l]) => <button key={k} className={review === k ? "is-on" : ""} onClick={() => setReview(k)}>{l}</button>)}
      </div>
      {err && <p className="muted">{err}</p>}
      {!rows ? <div className="spinner" /> : rows.length ? (
        <div className="cp-list">
          {rows.map((s) => (
            <Link key={s.id} to={`/levantamientos/${s.id}`} className="cp-row">
              <div className="cp-row__main">
                <b>{s.number} · {s.kind_label}</b>
                <div className="cp-row__meta">{s.customer || "—"}{s.site ? ` · ${s.site}` : ""}{s.opportunity ? ` · ${s.opportunity.number}` : ""}</div>
                <div className="cp-row__meta">{s.technician || "Sin técnico"} · {s.points_count} punto(s)</div>
              </div>
              <div className="cp-row__side">
                {s.review_status === "devuelto" ? <span className="badge badge--warn">Devuelto · {s.open_observations}</span> : s.pending_review ? <span className="badge badge--info">{s.pending_review.label}</span> : <Badge status={s.review_status || s.status} />}
              </div>
            </Link>
          ))}
        </div>
      ) : <Empty title="Aún no hay levantamientos de su empresa" hint="Se hacen sobre una oportunidad o un proyecto en común con Crimson." />}
      {nuevo && <NewSurvey oppId={Number(params.get("oportunidad")) || null} onClose={() => setParams({})} />}
    </>
  );
}
